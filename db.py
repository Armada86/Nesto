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
ALTER TABLE parts ADD COLUMN IF NOT EXISTS flip BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE projects ADD COLUMN IF NOT EXISTS settings JSONB;
ALTER TABLE projects ADD COLUMN IF NOT EXISTS cam JSONB;
CREATE TABLE IF NOT EXISTS cam_tools (
    id SERIAL PRIMARY KEY,
    number INT NOT NULL UNIQUE,
    name TEXT NOT NULL DEFAULT '',
    diameter DOUBLE PRECISION NOT NULL,
    flutes INT NOT NULL DEFAULT 2,
    rpm DOUBLE PRECISION NOT NULL,
    feed DOUBLE PRECISION NOT NULL,
    plunge DOUBLE PRECISION NOT NULL,
    step_down DOUBLE PRECISION NOT NULL,
    coolant TEXT NOT NULL DEFAULT 'none'
);
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
ALTER TABLE nest_runs ADD COLUMN IF NOT EXISTS seconds DOUBLE PRECISION;
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


def project_exists(name: str, exclude_id: int | None = None) -> bool:
    """Is there a project with this name (case-insensitive)? exclude_id: ignore that project (for renaming)."""
    with connect() as c:
        if exclude_id is None:
            row = c.execute("SELECT 1 FROM projects WHERE lower(name)=lower(%s)", (name,)).fetchone()
        else:
            row = c.execute("SELECT 1 FROM projects WHERE lower(name)=lower(%s) AND id<>%s", (name, exclude_id)).fetchone()
        return row is not None


def rename_project(pid: int, name: str):
    """Give project `pid` a new name. Its parts, settings and runs are untouched."""
    with connect() as c:
        c.execute("UPDATE projects SET name=%s WHERE id=%s", (name, pid))


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
            "SELECT name, shape, width, height, qty, rotate, flip, outline FROM parts WHERE project_id=%s ORDER BY id", (pid,)
        ).fetchall()


def save_parts(pid: int, parts: list[dict]):
    """Replace all parts of a project."""
    with connect() as c:
        c.execute("DELETE FROM parts WHERE project_id=%s", (pid,))
        for p in parts:
            c.execute(
                "INSERT INTO parts(project_id,name,shape,width,height,qty,rotate,flip,outline) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (pid, p["name"], p.get("shape", "rectangle"), p["width"], p["height"], p["qty"], p["rotate"], p.get("flip", True),
                 json.dumps(p["outline"]) if p.get("outline") else None),
            )


def save_run(pid, sheet_w, sheet_h, margin, spacing, sheets, utilization, result, seconds=None):
    """Store a nest result as a run of the project; returns the new run's id."""
    with connect() as c:
        row = c.execute(
            "INSERT INTO nest_runs(project_id,sheet_w,sheet_h,margin,spacing,sheets,utilization,result,seconds)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
            (pid, sheet_w, sheet_h, margin, spacing, sheets, utilization, json.dumps(result), seconds),
        ).fetchone()
        return row["id"]


def update_run_result(pid: int, run_id: int, result) -> int:
    """Replace the stored layout of a run (after parts were moved by hand). Only runs of project `pid`."""
    with connect() as c:
        return c.execute("UPDATE nest_runs SET result=%s WHERE id=%s AND project_id=%s",
                         (json.dumps(result), run_id, pid)).rowcount


def list_runs(pid: int):
    with connect() as c:
        return c.execute(
            "SELECT id, created_at, sheet_w, sheet_h, sheets, utilization, seconds FROM nest_runs"
            " WHERE project_id=%s ORDER BY id DESC", (pid,)
        ).fetchall()


def delete_runs(pid: int, run_ids) -> int:
    """Delete the given runs of project `pid` (runs of other projects are never touched). Returns how many."""
    ids = [int(i) for i in run_ids]
    if not ids:
        return 0
    with connect() as c:
        return c.execute("DELETE FROM nest_runs WHERE project_id=%s AND id = ANY(%s)", (pid, ids)).rowcount


def load_run(run_id: int):
    with connect() as c:
        return c.execute("SELECT * FROM nest_runs WHERE id=%s", (run_id,)).fetchone()


# ---------------------------------------------------------------------------------------------- machining (CAM)
def list_tools():
    """The tool library (one library for all projects), ordered by tool number."""
    with connect() as c:
        return c.execute("SELECT number, name, diameter, flutes, rpm, feed, plunge, step_down, coolant FROM cam_tools"
                         " ORDER BY number").fetchall()


def save_tools(tools: list[dict]):
    """Replace the whole tool library."""
    with connect() as c:
        c.execute("DELETE FROM cam_tools")
        for t in tools:
            c.execute("INSERT INTO cam_tools(number,name,diameter,flutes,rpm,feed,plunge,step_down,coolant)"
                      " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                      (int(t["number"]), t.get("name", ""), float(t["diameter"]), int(t.get("flutes", 2)), float(t["rpm"]),
                       float(t["feed"]), float(t["plunge"]), float(t["step_down"]), t.get("coolant", "none")))


def get_cam(pid: int):
    """The machining settings of a project (dict) or None."""
    with connect() as c:
        row = c.execute("SELECT cam FROM projects WHERE id=%s", (pid,)).fetchone()
        return row["cam"] if row else None


def save_cam(pid: int, settings: dict):
    with connect() as c:
        c.execute("UPDATE projects SET cam=%s WHERE id=%s", (json.dumps(settings), pid))
