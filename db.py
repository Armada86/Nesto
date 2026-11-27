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
ALTER TABLE nest_runs ADD COLUMN IF NOT EXISTS options JSONB;
CREATE TABLE IF NOT EXISTS app_migrations (name TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS app_settings (name TEXT PRIMARY KEY, value JSONB NOT NULL);
CREATE TABLE IF NOT EXISTS nesto_tools (
    number INT PRIMARY KEY,
    tool_type TEXT NOT NULL DEFAULT 'END MILL',
    name TEXT NOT NULL DEFAULT '',
    diameter DOUBLE PRECISION NOT NULL,
    shoulder_diameter DOUBLE PRECISION NOT NULL,
    arbor_diameter DOUBLE PRECISION NOT NULL,
    shoulder_angle DOUBLE PRECISION NOT NULL DEFAULT 0,
    cutting_length DOUBLE PRECISION NOT NULL,
    shoulder_length DOUBLE PRECISION NOT NULL,
    total_length DOUBLE PRECISION NOT NULL,
    chamfer DOUBLE PRECISION NOT NULL DEFAULT 0,
    outside_holder DOUBLE PRECISION NOT NULL,
    helical_angle DOUBLE PRECISION NOT NULL DEFAULT 30,
    rough BOOLEAN NOT NULL DEFAULT FALSE,
    flutes INT NOT NULL DEFAULT 2,
    holder TEXT NOT NULL DEFAULT ''
);
ALTER TABLE nesto_tools ADD COLUMN IF NOT EXISTS holder TEXT NOT NULL DEFAULT '';
ALTER TABLE nesto_tools ADD COLUMN IF NOT EXISTS point_angle DOUBLE PRECISION;
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
CREATE TABLE IF NOT EXISTS cam_operations (
    id SERIAL PRIMARY KEY,
    run_id INT NOT NULL REFERENCES nest_runs(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    settings JSONB NOT NULL DEFAULT '{}'::jsonb,
    created TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (run_id, name)
);
ALTER TABLE cam_operations ADD COLUMN IF NOT EXISTS position INT;
UPDATE cam_operations SET position = id WHERE position IS NULL;
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
        # one time: the tools saved by the first (switched-off) Machining tab are deleted; the NestoCAM Tools tab has its own table
        if not c.execute("SELECT 1 FROM app_migrations WHERE name='purge_old_cam_tools'").fetchone():
            c.execute("DELETE FROM cam_tools")
            c.execute("INSERT INTO app_migrations(name) VALUES ('purge_old_cam_tools')")


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


def latest_run_settings(pid: int) -> dict:
    """The sheet and nesting settings of the newest run of the project (widget keys sheet_w, sheet_h, margin, spacing, rot_label,
    prec_label, effort_label; the ones the run does not have are left out); {} when the project has no run."""
    with connect() as c:
        row = c.execute("SELECT sheet_w, sheet_h, margin, spacing, options FROM nest_runs WHERE project_id=%s ORDER BY id DESC LIMIT 1",
                        (pid,)).fetchone()
    if not row:
        return {}
    out = {k: float(row[k]) for k in ("sheet_w", "sheet_h", "margin", "spacing") if row.get(k) is not None}
    out.update({k: v for k, v in (row.get("options") or {}).items() if k in ("rot_label", "prec_label", "effort_label")})
    return out


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


def save_run(pid, sheet_w, sheet_h, margin, spacing, sheets, utilization, result, seconds=None, options=None):
    """Store a nest result as a run of the project; returns the new run's id. options: the nesting options of the run (rotation step,
    precision, search effort) as a dict, so that loading the run shows them again."""
    with connect() as c:
        row = c.execute(
            "INSERT INTO nest_runs(project_id,sheet_w,sheet_h,margin,spacing,sheets,utilization,result,seconds,options)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
            (pid, sheet_w, sheet_h, margin, spacing, sheets, utilization, json.dumps(result), seconds,
             json.dumps(options) if options else None),
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


TOOL_FIELDS = ("number", "tool_type", "name", "diameter", "shoulder_diameter", "arbor_diameter", "shoulder_angle", "cutting_length",
               "shoulder_length", "total_length", "chamfer", "outside_holder", "helical_angle", "flutes", "holder", "point_angle")


def list_cam_tools():
    """The NestoCAM tool library (one library for all projects), ordered by tool number."""
    with connect() as c:
        return c.execute(f"SELECT {', '.join(TOOL_FIELDS)} FROM nesto_tools ORDER BY number").fetchall()


def save_cam_tool(tool: dict, old_number: int | None = None):
    """Add a tool, or change the tool that had `old_number` (the number itself may change). Raises ValueError if the number is taken."""
    with connect() as c:
        taken = c.execute("SELECT 1 FROM nesto_tools WHERE number=%s", (int(tool["number"]),)).fetchone()
        if taken and (old_number is None or int(old_number) != int(tool["number"])):
            raise ValueError(f"Tool number {int(tool['number'])} is already used.")
        if old_number is not None:
            c.execute("DELETE FROM nesto_tools WHERE number=%s", (int(old_number),))
        c.execute(f"INSERT INTO nesto_tools({', '.join(TOOL_FIELDS)}) VALUES ({', '.join(['%s'] * len(TOOL_FIELDS))})",
                  [tool.get(f) for f in TOOL_FIELDS])


def delete_cam_tool(number: int):
    with connect() as c:
        c.execute("DELETE FROM nesto_tools WHERE number=%s", (int(number),))


def list_operations(run_id: int):
    """The CAM operations of a run in the order that was set (new operations come last)."""
    with connect() as c:
        return c.execute("SELECT id, name, settings FROM cam_operations WHERE run_id=%s ORDER BY COALESCE(position, id), id", (run_id,)).fetchall()


def set_operation_order(run_id: int, ids: list) -> None:
    """Put the operations of the run in this order (ids: the operation ids, first to last); operations that are not named keep their place after them."""
    with connect() as c:
        for n, i in enumerate(ids, 1):
            c.execute("UPDATE cam_operations SET position=%s WHERE id=%s AND run_id=%s", (n, int(i), run_id))
        rest = c.execute("SELECT id FROM cam_operations WHERE run_id=%s AND id <> ALL(%s) ORDER BY COALESCE(position, id), id",
                         (run_id, [int(i) for i in ids] or [0])).fetchall()
        for k, r in enumerate(rest, len(ids) + 1):
            c.execute("UPDATE cam_operations SET position=%s WHERE id=%s", (k, r["id"]))


def get_operation(op_id: int):
    with connect() as c:
        return c.execute("SELECT id, run_id, name, settings FROM cam_operations WHERE id=%s", (op_id,)).fetchone()


def create_operation(run_id: int, name: str, settings: dict) -> int:
    """A new operation in a run. Raises ValueError if the run already has an operation with this name (any capitals)."""
    with connect() as c:
        if c.execute("SELECT 1 FROM cam_operations WHERE run_id=%s AND lower(name)=lower(%s)", (run_id, name)).fetchone():
            raise ValueError(f"This run already has an operation called {name}.")
        return c.execute("INSERT INTO cam_operations(run_id, name, settings, position) VALUES (%s,%s,%s,"
                         " COALESCE((SELECT MAX(COALESCE(position, id)) FROM cam_operations WHERE run_id=%s), 0) + 1) RETURNING id",
                         (run_id, name, json.dumps(settings), run_id)).fetchone()["id"]


def save_operation_settings(op_id: int, settings: dict):
    with connect() as c:
        c.execute("UPDATE cam_operations SET settings=%s WHERE id=%s", (json.dumps(settings), op_id))


def rename_operation(op_id: int, name: str):
    """Raises ValueError if another operation of the same run has this name."""
    with connect() as c:
        row = c.execute("SELECT run_id FROM cam_operations WHERE id=%s", (op_id,)).fetchone()
        if row and c.execute("SELECT 1 FROM cam_operations WHERE run_id=%s AND lower(name)=lower(%s) AND id<>%s",
                             (row["run_id"], name, op_id)).fetchone():
            raise ValueError(f"This run already has an operation called {name}.")
        c.execute("UPDATE cam_operations SET name=%s WHERE id=%s", (name, op_id))


def get_app_setting(name: str):
    """A setting of the whole app (not of a project): its value, or None."""
    with connect() as c:
        row = c.execute("SELECT value FROM app_settings WHERE name=%s", (name,)).fetchone()
        return row["value"] if row else None


def save_app_setting(name: str, value):
    with connect() as c:
        c.execute("INSERT INTO app_settings(name, value) VALUES (%s,%s) ON CONFLICT (name) DO UPDATE SET value=EXCLUDED.value",
                  (name, json.dumps(value)))


def delete_autocam_operations(run_id: int, sheet_no: int) -> int:
    """Delete the operations that AutoCam made for this sheet of the run (the operations made by hand stay). Returns how many."""
    with connect() as c:
        return c.execute("DELETE FROM cam_operations WHERE run_id=%s AND settings->>'autocam_sheet' = %s", (run_id, str(int(sheet_no)))).rowcount


def delete_operation(op_id: int):
    with connect() as c:
        c.execute("DELETE FROM cam_operations WHERE id=%s", (op_id,))


def latest_operation_settings(pid: int):
    """The settings of the newest operation of any run of the project (a new operation starts from them), or None."""
    with connect() as c:
        row = c.execute("SELECT o.settings FROM cam_operations o JOIN nest_runs r ON r.id=o.run_id WHERE r.project_id=%s"
                        " ORDER BY o.id DESC LIMIT 1", (pid,)).fetchone()
        return row["settings"] if row else None


def get_cam(pid: int):
    """The machining settings of a project (dict) or None."""
    with connect() as c:
        row = c.execute("SELECT cam FROM projects WHERE id=%s", (pid,)).fetchone()
        return row["cam"] if row else None


def save_cam(pid: int, settings: dict):
    with connect() as c:
        c.execute("UPDATE projects SET cam=%s WHERE id=%s", (json.dumps(settings), pid))
