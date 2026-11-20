"""Neon (Postgres) storage. Connection string: env DATABASE_URL or st.secrets['DATABASE_URL']."""
from __future__ import annotations

import json
import os

import psycopg
from psycopg.rows import dict_row

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS parts (
    id SERIAL PRIMARY KEY,
    project_id INT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    width DOUBLE PRECISION NOT NULL,
    height DOUBLE PRECISION NOT NULL,
    qty INT NOT NULL DEFAULT 1,
    rotate BOOLEAN NOT NULL DEFAULT TRUE,
    outline JSONB
);
ALTER TABLE parts ADD COLUMN IF NOT EXISTS shape TEXT NOT NULL DEFAULT 'rectangle';
ALTER TABLE projects ADD COLUMN IF NOT EXISTS settings JSONB;
CREATE TABLE IF NOT EXISTS nest_runs (
    id SERIAL PRIMARY KEY,
    project_id INT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    sheet_w DOUBLE PRECISION NOT NULL,
    sheet_h DOUBLE PRECISION NOT NULL,
    margin DOUBLE PRECISION NOT NULL,
    spacing DOUBLE PRECISION NOT NULL,
    sheets INT NOT NULL,
    utilization DOUBLE PRECISION NOT NULL,
    result JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


def get_url() -> str | None:
    url = os.environ.get("DATABASE_URL")
    if url:
        return url
    try:
        import streamlit as st
        return st.secrets.get("DATABASE_URL")
    except Exception:  # noqa: BLE001
        return None


def connect():
    url = get_url()
    if not url:
        raise RuntimeError("DATABASE_URL is not set (env var or .streamlit/secrets.toml)")
    return psycopg.connect(url, row_factory=dict_row)


def init_db():
    with connect() as c:
        c.execute(SCHEMA)


def list_projects():
    with connect() as c:
        return c.execute("SELECT id, name FROM projects ORDER BY name").fetchall()


def create_project(name: str) -> int:
    with connect() as c:
        row = c.execute(
            "INSERT INTO projects(name) VALUES (%s) ON CONFLICT (name) DO UPDATE SET name=EXCLUDED.name RETURNING id",
            (name,),
        ).fetchone()
        return row["id"]


def project_exists(name: str) -> bool:
    with connect() as c:
        return c.execute("SELECT 1 FROM projects WHERE lower(name)=lower(%s)", (name,)).fetchone() is not None


def get_project(pid: int):
    """{"id", "name", "settings"} or None."""
    with connect() as c:
        return c.execute("SELECT id, name, settings FROM projects WHERE id=%s", (pid,)).fetchone()


def save_settings(pid: int, settings: dict):
    with connect() as c:
        c.execute("UPDATE projects SET settings=%s WHERE id=%s", (json.dumps(settings), pid))


def delete_project(pid: int):
    with connect() as c:
        c.execute("DELETE FROM projects WHERE id=%s", (pid,))


def load_parts(pid: int):
    with connect() as c:
        return c.execute(
            "SELECT name, shape, width, height, qty, rotate, outline FROM parts WHERE project_id=%s ORDER BY id", (pid,)
        ).fetchall()


def save_parts(pid: int, parts: list[dict]):
    """Replace all parts of a project."""
    with connect() as c:
        c.execute("DELETE FROM parts WHERE project_id=%s", (pid,))
        for p in parts:
            c.execute(
                "INSERT INTO parts(project_id,name,shape,width,height,qty,rotate,outline) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                (pid, p["name"], p.get("shape", "rectangle"), p["width"], p["height"], p["qty"], p["rotate"],
                 json.dumps(p["outline"]) if p.get("outline") else None),
            )


def save_run(pid, sheet_w, sheet_h, margin, spacing, sheets, utilization, result):
    with connect() as c:
        c.execute(
            "INSERT INTO nest_runs(project_id,sheet_w,sheet_h,margin,spacing,sheets,utilization,result)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            (pid, sheet_w, sheet_h, margin, spacing, sheets, utilization, json.dumps(result)),
        )


def list_runs(pid: int):
    with connect() as c:
        return c.execute(
            "SELECT id, created_at, sheet_w, sheet_h, sheets, utilization FROM nest_runs"
            " WHERE project_id=%s ORDER BY id DESC", (pid,)
        ).fetchall()


def load_run(run_id: int):
    with connect() as c:
        return c.execute("SELECT * FROM nest_runs WHERE id=%s", (run_id,)).fetchone()
