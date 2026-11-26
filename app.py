import copy
import json
import re
import time

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from fresh_modules import reload_changed

# after a deploy the server may keep old copies of the local modules in memory: reload the ones that changed
reload_changed(["machine_profile", "shapes", "nesting", "layout_edit", "nesting_poly", "render", "dxf_io", "step_io",
                "dxf_export", "step_export", "cutlist_export", "part_view", "sheet_editor", "nest_job", "cam", "post_fanuc",
                "cam_ui", "camming_ui", "tools_ui", "data_ui", "levels_ui", "gcode_cam", "gcode_ui", "branding", "db"])

import branding
import db
from cutlist_export import cutlist_xlsx
from dxf_export import all_sheets_dxf, sheet_dxf, sheets_zip
from nest_job import NestJob, start_job
from step_export import all_sheets_step, sheet_step, sheets_step_zip
from layout_edit import apply_move, apply_transform, ensure_polys, is_held
from dxf_io import read_dxf
from nesting import Part, Placement, Sheet, nest, summary
from nesting_poly import PolyPart, nest_polygons, scrap_rectangles
from part_view import fmt as fmt_dim, part_png, part_stats
from render import draw_sheet, part_color_indices
from cam_ui import machining_tab
from camming_ui import camming_tab
from step_io import read_step
from sheet_editor import sheet_editor
from shapes import SHAPES, geom_to_json, part_geometry, stored_geometry

def fmt_time(sec):
    """12.3 s / 1 min 49 s / 1 h 02 min"""
    if sec is None:
        return "–"
    if sec < 1:
        return "< 1 s"
    if sec < 60:
        return f"{sec:.0f} s" if sec >= 10 else f"{sec:.1f} s"
    m, s = divmod(int(round(sec)), 60)
    if m < 60:
        return f"{m} min {s:02d} s"
    return f"{m // 60} h {m % 60:02d} min"


st.set_page_config(page_title="Nesto – 2D Nesting", layout="wide", page_icon=branding.PAGE_ICON)
st.markdown(branding.CSS, unsafe_allow_html=True)

# ---------- database ----------
if not db.get_url():
    st.error("Set DATABASE_URL (your Neon connection string) in `.streamlit/secrets.toml` or as an env var.")
    st.stop()
try:
    db.init_db()
except Exception as e:  # noqa: BLE001
    st.error(f"Cannot connect to Neon: {e}")
    st.stop()

ROT_STEPS = {"None": 0, "90°": 90, "45°": 45, "30°": 30, "15°": 15, "10°": 10, "5°": 5, "2°": 2, "1°": 1}
PRECISION = {"Fast": 500, "Balanced": 800, "Fine": 1400}
EFFORT = {"Quick": 0, "Normal": 15, "Thorough": 60}  # seconds of extra search for irregular parts

# settings stored with every project (widget keys)
DEFAULTS = {"sheet_w": 2500.0, "sheet_h": 1250.0, "margin": 5.0, "spacing": 3.0,
            "rot_label": "90°", "prec_label": "Balanced", "effort_label": "Normal"}
for _k, _v in DEFAULTS.items():
    st.session_state.setdefault(_k, _v)


# ---------- project handling ----------
def open_project(pid: int | None):
    """Make `pid` the open project (None = close). Settings are applied before the widgets are drawn."""
    for k in ("result", "nest_info", "parts", "loaded_pid", "lib_idx", "lib_search", "result_original", "run_id",
              "edit_history", "edit_rev"):
        st.session_state.pop(k, None)
    st.session_state.pid = pid
    if pid is None:
        st.query_params.clear()
        st.session_state.pending_settings = {}
        return
    st.session_state.pending_settings = (db.get_project(pid) or {}).get("settings") or {}
    st.query_params["project"] = str(pid)


def unsaved_moves() -> int:
    """Parts moved by hand in the layout on the screen and not saved yet."""
    return len(st.session_state.get("edit_history", []))


def leave_guard(n: int) -> None:
    """Make the browser ask before the page is reloaded / closed / left while there are unsaved moves.
    (Always rendered: with n = 0 it removes the handler again.)"""
    html = ("<script>(function(){var w=window.parent;"
            "if(w.__nestoGuard){w.removeEventListener('beforeunload',w.__nestoGuard);w.__nestoGuard=null;}"
            f"if({int(n)}>0){{w.__nestoGuard=function(e){{e.preventDefault();e.returnValue='';return '';}};"
            "w.addEventListener('beforeunload',w.__nestoGuard);}})();</script>")
    if hasattr(st, "iframe"):                       # st.components.v1.html is being removed from Streamlit
        st.iframe(html, height=1)
    else:
        components.html(html, height=0)


@st.dialog("New project")
def new_project_dialog():
    name = st.text_input("Project name", placeholder="e.g. Job 1042 – brackets")
    n = unsaved_moves()
    ok = st.checkbox(f"Discard the {n} unsaved move{'s' if n != 1 else ''} of the current layout", key="discard_new") if n else True
    if st.button("Create project", type="primary", width="stretch", disabled=not ok):
        name = name.strip()
        if not name:
            st.error("Please enter a name.")
        elif db.project_exists(name):
            st.error("A project with this name already exists.")
        else:
            open_project(db.create_project(name))
            st.rerun()


@st.dialog("Load project")
def load_project_dialog():
    projects = db.list_projects()
    if not projects:
        st.info("There are no projects yet. Create one with **New**.")
        return
    labels = {p["name"]: p["id"] for p in projects}
    current_name = st.session_state.get("pid_name")
    idx = list(labels).index(current_name) if current_name in labels else 0
    choice = st.selectbox("Select a project", list(labels), index=idx)
    n = unsaved_moves()
    ok = st.checkbox(f"Discard the {n} unsaved move{'s' if n != 1 else ''} of the current layout", key="discard_load") if n else True
    if st.button("Open project", type="primary", width="stretch", disabled=not ok):
        open_project(labels[choice])
        st.rerun()


@st.dialog("Rename project")
def rename_project_dialog(pid: int, current: str):
    name = st.text_input("New name", value=current)
    if st.button("Rename project", type="primary", width="stretch"):
        new = name.strip()
        if not new:
            st.error("Please enter a name.")
        elif new == current:
            st.info("That is already the name of this project.")
        elif db.project_exists(new, exclude_id=pid):
            st.error("Another project already has this name.")
        else:
            try:
                db.rename_project(pid, new)
            except Exception:  # noqa: BLE001  (e.g. a name that differs only in case from an existing one)
                st.error("Another project already has this name.")
                return
            st.session_state.flash = (f"Renamed “{current}” to “{new}”.", ":material/edit:")
            st.rerun()


@st.dialog("Delete project")
def delete_project_dialog(pid: int, name: str):
    st.warning(f"Delete **{name}** with all its parts and nesting history? This cannot be undone.")
    d1, d2 = st.columns(2)
    if d1.button("Cancel", width="stretch"):
        st.rerun()
    if d2.button("Delete", type="primary", width="stretch"):
        db.delete_project(pid)
        open_project(None)
        st.rerun()


def set_result(result, run_id=None):
    """Show `result` (a list of sheets) and start a fresh editing session for it."""
    st.session_state.result = result
    st.session_state.result_original = copy.deepcopy(result)   # "Reset layout" returns to this
    st.session_state.run_id = run_id                           # the stored run these sheets belong to
    st.session_state.edit_history = []                         # moves made by hand since: [(sheet, part, dx, dy)]
    st.session_state.edit_rev = {}                             # per sheet: number of changes (guards against stale drags)
    st.session_state.result_token = st.session_state.get("result_token", 0) + 1


def save_as_new_run():
    """Store the moved layout as a NEW run; the run it came from keeps its original layout."""
    result = st.session_state.result
    meta = st.session_state.get("run_meta") or {"margin": margin, "spacing": spacing}
    s = summary(_sheets_from(result))
    new_id = db.save_run(pid, result[0]["width"], result[0]["height"], meta["margin"], meta["spacing"],
                         s["sheets"], s["utilization"], result, None)
    st.session_state.run_id = new_id
    st.session_state.result_original = copy.deepcopy(result)
    st.session_state.edit_history = []
    return new_id


@st.dialog("Unsaved moves")
def unsaved_dialog(action: str, payload=None):
    """The layout on the screen has parts moved by hand and not saved; `action` ("load_run" / "nest") would replace it."""
    n = unsaved_moves()
    what = {"load_run": "Loading another run replaces it.", "nest": "Nesting again replaces it."}[action]
    st.warning(f"You moved parts by hand ({n} move{'s' if n != 1 else ''}) and have not saved them. {what}")
    run_id = st.session_state.get("run_id")
    c1, c2, c3 = st.columns(3)
    if st.button("💾 Save as new run & continue", width="stretch", help="Keep the original run as it is and store the moved layout as a new run."):
        save_as_new_run()
        st.session_state.pending_action = (action, payload)
        st.rerun()
    if c1.button("💾 Save & continue", type="primary", width="stretch", disabled=not run_id,
                 help="Store the moved layout in its run first." if run_id else "This layout has no stored run to save to."):
        db.update_run_result(pid, run_id, st.session_state.result)
        st.session_state.result_original = copy.deepcopy(st.session_state.result)
        st.session_state.edit_history = []
        st.session_state.pending_action = (action, payload)
        st.rerun()
    if c2.button("Discard & continue", width="stretch"):
        st.session_state.edit_history = []
        st.session_state.pending_action = (action, payload)
        st.rerun()
    if c3.button("Cancel", width="stretch"):
        st.rerun()


def nest_options() -> dict:
    """The nesting options on the screen (stored with a run, so that loading the run shows them again)."""
    return {k: st.session_state[k] for k in ("rot_label", "prec_label", "effort_label")}


def do_load_run(run_id):
    run_row = db.load_run(run_id)
    set_result(run_row["result"], run_id)
    st.session_state.nest_seconds = run_row.get("seconds")
    st.session_state.pop("nest_info", None)
    st.session_state.pop("nest_parts", None)   # the part list of that old run is not stored
    st.session_state.run_meta = {"margin": run_row.get("margin", margin), "spacing": run_row.get("spacing", spacing)}
    # the sheet and the nesting options of the run come back into their boxes (applied at the start of the next run of the script,
    # before the boxes exist: a widget cannot be changed after it was drawn)
    new = settings_snapshot()
    for k in ("sheet_w", "sheet_h", "margin", "spacing"):
        if run_row.get(k) is not None:
            new[k] = float(run_row[k])
    opts = run_row.get("options") or {}
    for k, allowed in (("rot_label", ROT_STEPS), ("prec_label", PRECISION), ("effort_label", EFFORT)):
        if opts.get(k) in allowed:
            new[k] = opts[k]
    if new != settings_snapshot():
        st.session_state.pending_settings = new
        try:
            db.save_settings(pid, new)
        except Exception as e:  # noqa: BLE001
            st.toast(f"Could not save the settings: {e}", icon=":material/error:")
        st.rerun()


def settings_snapshot():
    return {k: st.session_state[k] for k in DEFAULTS}


def autosave_settings():
    """Called the moment a sheet / nesting setting changes: store it with the open project right away."""
    pid_ = st.session_state.get("pid")
    if not pid_:
        return
    try:
        db.save_settings(pid_, settings_snapshot())
    except Exception as e:  # noqa: BLE001
        st.toast(f"Could not save the settings: {e}", icon=":material/error:")


@st.dialog("Delete runs from the history")
def delete_runs_dialog(pid: int, options: dict):
    """options: {label: run id} of this project's runs; the multiselect state is st.session_state.del_runs_sel."""
    st.multiselect("Runs to delete", list(options), key="del_runs_sel", placeholder="Select the runs to delete…")
    st.button("Select all", on_click=lambda: st.session_state.__setitem__("del_runs_sel", list(options)))
    chosen = st.session_state.get("del_runs_sel", [])
    st.caption("This cannot be undone. A result already shown on the screen stays until you nest again.")
    c1, c2 = st.columns(2)
    if c1.button("Cancel", width="stretch"):
        st.rerun()
    if c2.button(f"Delete {len(chosen)} run{'s' if len(chosen) != 1 else ''}", type="primary", width="stretch",
                 disabled=not chosen):
        n = db.delete_runs(pid, [options[l] for l in chosen])
        st.session_state.flash = (f"Deleted {n} run{'s' if n != 1 else ''} from the history.", ":material/delete:")
        st.rerun()


# reopen the project from the page address after a browser reload
if "pid" not in st.session_state:
    qp = st.query_params.get("project")
    if qp and str(qp).isdigit() and db.get_project(int(qp)):
        open_project(int(qp))
    else:
        st.session_state.pid = None
if "pending_settings" in st.session_state:  # apply stored settings before the widgets exist
    cfg = st.session_state.pop("pending_settings") or {}
    for k, default in DEFAULTS.items():
        st.session_state[k] = cfg.get(k, default)

pid = st.session_state.pid
proj = db.get_project(pid) if pid else None
if pid and not proj:  # deleted elsewhere
    open_project(None)
    st.rerun()
st.session_state.pid_name = proj["name"] if proj else None

# ---------- sidebar ----------
with st.sidebar:
    st.markdown(branding.header_html("2D nesting", size="small"), unsafe_allow_html=True)
    if proj:
        st.markdown(f"**{proj['name']}**")
    else:
        st.markdown("_No project open_")
    r1a, r1b = st.columns(2)
    r2a, r2b = st.columns(2)
    if r1a.button("New", icon=":material/note_add:", width="stretch"):
        new_project_dialog()
    if r1b.button("Load", icon=":material/folder_open:", width="stretch"):
        load_project_dialog()
    save_clicked = r2a.button("Save", icon=":material/save:", type="primary", width="stretch", disabled=not proj)
    if r2b.button("Rename", icon=":material/edit:", width="stretch", disabled=not proj):
        rename_project_dialog(pid, proj["name"])
    if st.button("Delete", icon=":material/delete:", width="stretch", disabled=not proj):
        delete_project_dialog(pid, proj["name"])

if not proj:
    st.markdown(branding.header_html("2D nesting"), unsafe_allow_html=True)
    st.info("Start a new project with **New**, or open an existing one with **Load** (left menu).")
    st.stop()

st.markdown(branding.header_html(proj["name"]), unsafe_allow_html=True)

# ---------- parts ----------
if st.session_state.get("loaded_pid") != pid:
    st.session_state.parts = db.load_parts(pid)
    st.session_state.loaded_pid = pid
parts_state: list[dict] = st.session_state.parts

# The Machining tab (tool library, toolpaths, G-code: cam.py, post_fanuc.py, cam_ui.py) is switched off for now while its
# flow is being redesigned: set SHOW_MACHINING = True to bring it back. Nothing of it was removed.
SHOW_MACHINING = False
_tab_names = ["1 · Import files", "2 · Parts details", "3 · Nesting", "4 · NestoCAM"] + (["Machining"] if SHOW_MACHINING else [])
_tabs = st.tabs(_tab_names)
tab_dxf, tab_lib, tab_table, tab_camming = _tabs[:4]
tab_cam = _tabs[4] if SHOW_MACHINING else None

with tab_table:
    with st.expander("Expand parts"):          # the table is folded away: one line until you open it
        st.caption("Size A = width (or diameter for a circle). Size B = height (ignored for circles). "
                   "Triangle: A = base, B = height. Parts from DXF keep their drawing; their sizes are informational.")
        cols = ["name", "shape", "width", "height", "qty", "rotate", "flip"]
        df = pd.DataFrame(
            [{k: p.get(k, {"shape": "rectangle", "rotate": True, "flip": True}.get(k)) for k in cols} for p in parts_state]
            or [{"name": "", "shape": "rectangle", "width": 0.0, "height": 0.0, "qty": 1, "rotate": True, "flip": True}]
        )
        df.index = range(1, len(df) + 1)                      # serial numbers 1, 2, 3 ... shown as the first column
        edited = st.data_editor(
            df, num_rows="dynamic", width="stretch", key=f"editor_{pid}_{len(parts_state)}", hide_index=False,
            column_config={
                "_index": st.column_config.Column("No.", width=48, disabled=True, alignment="left"),
                "name": "Name",
                "shape": st.column_config.SelectboxColumn("Shape", options=SHAPES, default="rectangle", required=True),
                "width": st.column_config.NumberColumn("Size A", min_value=0.0),
                "height": st.column_config.NumberColumn("Size B", min_value=0.0),
                "qty": st.column_config.NumberColumn("Qty", min_value=0, step=1,
                                                     help="Number of pieces to nest. 0 = keep the part in the project but do not nest it."),
                "rotate": st.column_config.CheckboxColumn("Can rotate", default=True),
                "flip": st.column_config.CheckboxColumn("Can flip", default=True,
                                                        help="The part may be turned over (mirrored) to nest tighter. "
                                                             "Leave on when both sides of the sheet are the same."),
            },
        )
        outlines = {p["name"]: p.get("outline") for p in parts_state}
        current = []
        for i, (_, r) in enumerate(edited.iterrows()):
            shape = r["shape"] if isinstance(r["shape"], str) else "rectangle"
            w = float(r["width"]) if pd.notna(r["width"]) else 0.0
            h = float(r["height"]) if pd.notna(r["height"]) else 0.0
            if shape == "circle":
                h = w
            if w <= 0 or h <= 0:
                continue
            nm = str(r["name"]) if isinstance(r["name"], str) and r["name"] else f"part{i + 1}"
            current.append({"name": nm, "shape": shape, "width": w, "height": h,
                            "qty": int(r["qty"]) if pd.notna(r["qty"]) else 1,
                            "rotate": bool(r["rotate"]),
                            "flip": bool(r["flip"]) if pd.notna(r["flip"]) else True,   # old rows / new rows: on
                            "outline": outlines.get(nm)})

    # the sheet and the nesting options belong to the run: they sit in the Nesting tab, above the Nest button
    col_hist, col_set = st.columns([1.3, 3])
    history_box = col_hist.container(border=True)           # the run history (filled further down), the first column
    with col_set.container(border=True):
        st.caption("Sheet and nesting settings are saved with the project automatically.")
        col_sheet, col_nest = st.columns(2)
        with col_sheet:
            st.subheader("Sheet")
            sheet_w = st.number_input("Length", min_value=1.0, step=10.0, key="sheet_w", on_change=autosave_settings,
                                      help="Sheet length: the horizontal side in the drawings.")
            sheet_h = st.number_input("Width", min_value=1.0, step=10.0, key="sheet_h", on_change=autosave_settings,
                                      help="Sheet width: the vertical side in the drawings.")
            margin = st.number_input("Edge margin", min_value=0.0, step=1.0, key="margin", on_change=autosave_settings)
            spacing = st.number_input("Part spacing (kerf)", min_value=0.0, step=0.5, key="spacing", on_change=autosave_settings)
        with col_nest:
            st.subheader("Nesting")
            rot_label = st.select_slider("Rotation step (irregular shapes)", list(ROT_STEPS), key="rot_label", on_change=autosave_settings,
                                         help="Finer steps pack irregular shapes better but take longer. Steps below 15° first search with 15° and then fine-tune "
                                              "the best layout with the finer angles around the angles it uses (this adds some time).")
            prec_label = st.select_slider("Precision (irregular shapes)", list(PRECISION), key="prec_label", on_change=autosave_settings,
                                          help="Finer = tighter packing, slower.")
            effort_label = st.select_slider("Search effort (irregular shapes)", list(EFFORT), key="effort_label",
                                            on_change=autosave_settings,
                                            help="Quick = one pass. Normal / Thorough keep trying many different layouts "
                                                 "(pairing, placement rules, random restarts) for 15 / 60 seconds and keep the best.")

with tab_dxf:
    st.caption("**DXF**: any closed outline made of LINE / ARC / CIRCLE / POLYLINE / SPLINE / ELLIPSE entities; "
               "inner closed contours become holes (other parts can nest inside them). "
               "**STEP**: flat (plate-like) parts - the largest flat face gives the outline and holes, "
               "any orientation, thickness ignored. One part per solid. Bent parts: export the flat pattern as DXF.")
    files = st.file_uploader("DXF / STEP files", type=["dxf", "step", "stp"], accept_multiple_files=True)
    c_a, c_b, c_c = st.columns(3)
    dxf_qty = c_a.number_input("Quantity per part", min_value=0, value=1, help="0 = add to the project but do not nest yet")
    dxf_scale = c_b.number_input("Scale (e.g. 25.4 for inch → mm)", min_value=0.0001, value=1.0, format="%.4f")
    split = c_c.checkbox("DXF: one part per closed contour", help="Use for DXFs that contain many separate parts.")
    if files and st.button("Add parts from files"):
        added = 0
        used = {p["name"] for p in parts_state}
        for f in files:
            try:
                if f.name.lower().endswith((".step", ".stp")):
                    found = read_step(f.getvalue(), scale=dxf_scale)
                else:
                    found = [(g, None) for g in read_dxf(f.getvalue(), one_part=not split, scale=dxf_scale)]
            except ValueError as e:
                st.error(f"{f.name}: {e}")
                continue
            geoms = [g for g, _ in found]
            for _, note in found:
                if note:
                    st.warning(f"{f.name}: {note}")
            base = f.name.rsplit(".", 1)[0]
            for k, g in enumerate(geoms, 1):
                nm = base if len(geoms) == 1 else f"{base}_{k}"
                while nm in used:
                    nm += "_"
                used.add(nm)
                x0, y0, x1, y1 = g.bounds
                parts_state.append({"name": nm, "shape": "dxf", "width": round(x1 - x0, 3),
                                    "height": round(y1 - y0, 3), "qty": int(dxf_qty), "rotate": True, "flip": True,
                                    "outline": {"polys": geom_to_json(g)}})
                added += 1
        if added:
            st.session_state.parts = parts_state
            db.save_parts(pid, parts_state)
            st.rerun()


if SHOW_MACHINING:
    with tab_cam:
        _run_no = st.session_state.get("run_id")
        _safe = re.sub(r"[^A-Za-z0-9_-]+", "_", proj["name"]).strip("_") or "nest"
        machining_tab(db, pid, proj["name"], st.session_state.get("result"),
                      st.session_state.get("run_meta") or {"margin": margin, "spacing": spacing}, _run_no,
                      f"{_safe}_run{_run_no}" if _run_no else _safe)


with tab_lib:
    if not current:
        st.info("This project has no parts yet. Add them in **Nesting** or **Import files**.")
    else:
        n_in = sum(1 for p in current if p["qty"] > 0)
        st.caption(f"{len(current)} parts in this project · {n_in} in the nesting list · "
                   f"{len(current) - n_in} not nested (quantity 0). Click a name to see its drawing.")
        idx = min(st.session_state.get("lib_idx", 0), len(current) - 1)
        left, right = st.columns([2, 3])
        with left:
            q = st.text_input("Search parts", placeholder="Search parts…", label_visibility="collapsed", key="lib_search")
            with st.container(height=560, border=True):
                shown = [i for i, p in enumerate(current) if q.strip().lower() in p["name"].lower()]
                if not shown:
                    st.caption("No part matches.")
                for i in shown:
                    p = current[i]
                    st.button(f"{p['name']}   ·   " + (f"× {p['qty']}" if p["qty"] > 0 else "not nested"),
                              key=f"lib_btn_{i}", width="stretch", type="primary" if i == idx else "secondary",
                              on_click=lambda i=i: st.session_state.__setitem__("lib_idx", i))
        with right:
            p = current[idx]
            g = part_geometry(p)
            stats = part_stats(g)
            status = (f"{p['qty']} pcs in the nesting list" if p["qty"] > 0 else "not in the nesting list (quantity 0)")
            st.image(part_png(g, p["name"]))                  # fixed-size box for every part
            st.caption(f"**{p['name']}** · {status} · "
                       + ("imported drawing" if p["shape"] == "dxf" else f"typed dimensions ({p['shape']})")
                       + (" · can be rotated" if p["rotate"] else " · fixed orientation")
                       + (" · can be flipped" if p.get("flip", True) else " · not flipped"))
            r1a, r1b = st.columns(2)
            r2a, r2b = st.columns(2)
            r1a.metric("Overall size", f"{fmt_dim(stats['width'])} × {fmt_dim(stats['height'])}")
            r1b.metric("Area", f"{stats['area']:,.0f}")
            r2a.metric("Cut length", f"{stats['cut_length']:,.0f}")
            r2b.metric("Holes", len(stats["holes"]))
            st.caption("Sizes are in the units of your drawing / typed dimensions (usually mm).")

@st.cache_resource
def nest_jobs() -> dict:
    """Background nesting jobs of the server (project id -> NestJob), shared by every session and page reload."""
    return {}


# an action the user confirmed in the "Unsaved moves" dialog
_pending = st.session_state.pop("pending_action", None)
if _pending and _pending[0] == "load_run":
    do_load_run(_pending[1])

with tab_table:
    st.markdown("""
<style>
/* big green Nest button, centred */
.st-key-nest_btn { width: 100% !important; display: flex !important; justify-content: center !important; margin: 0.6rem 0 1.2rem; }
.st-key-nest_btn [data-testid="stButton"] { width: auto !important; flex: 0 0 auto !important; }
.st-key-nest_btn button { width: auto !important; background-color: #16a34a !important; border: 2px solid #15803d !important; color: #fff !important;
    min-height: 5rem !important; padding: 0 2.6rem !important; border-radius: 0.9rem !important; }
.st-key-nest_btn button p { font-size: 1.9rem !important; font-weight: 600 !important; }
.st-key-nest_btn button:hover { background-color: #15803d !important; border-color: #166534 !important; }
.st-key-nest_btn button:active { background-color: #166534 !important; }
/* while editing parts: no greying out of the page during the short re-runs after each move */
[data-testid="stMain"]:has(iframe[src*="sheet_editor"]) [data-stale="true"] { opacity: 1 !important; transition: none !important; }
/* the four run numbers stay side by side, also on a phone (Streamlit would stack the columns) */
.st-key-metrics_row [data-testid="stHorizontalBlock"] { flex-wrap: nowrap !important; gap: 0.5rem !important; }
.st-key-metrics_row [data-testid="stColumn"] { min-width: 0 !important; width: auto !important; flex: 1 1 0 !important; }
.st-key-metrics_row [data-testid="stMetricValue"], .st-key-metrics_row [data-testid="stMetricValue"] > div { overflow: visible !important; text-overflow: clip !important; }
@media (max-width: 1100px) {
  .st-key-metrics_row [data-testid="stMetricValue"] { font-size: 1.7rem !important; }
}
@media (max-width: 640px) {
  .st-key-metrics_row [data-testid="stMetricLabel"] p { font-size: 0.62rem !important; white-space: nowrap; }
  .st-key-metrics_row [data-testid="stMetricValue"] { font-size: 1.05rem !important; }
  .st-key-metrics_row [data-testid="stHorizontalBlock"] { gap: 0.25rem !important; }
}
/* the three main tabs look like bordered cards */
[data-testid="stTabs"] [role="tablist"] { gap: 0.5rem !important; border-bottom: none !important; box-shadow: none !important; }
[data-testid="stTabs"] [role="tablist"]::after { display: none !important; }
[data-testid="stTabs"] .react-aria-SelectionIndicator, [data-testid="stTabs"] [data-baseweb="tab-highlight"],
[data-testid="stTabs"] [data-baseweb="tab-border"] { display: none !important; }
[data-testid="stTabs"] [role="tab"] { border: 1px solid #cbd5e1 !important; border-radius: 0.6rem !important; background: #f8fafc !important;
    padding: 0.55rem 1.1rem !important; height: auto !important; box-shadow: 0 1px 2px rgba(15,23,42,.08); }
[data-testid="stTabs"] [role="tab"]:hover { background: #e6f3fb !important; }
[data-testid="stTabs"] [role="tab"][aria-selected="true"] { background: #fff !important; border: 2px solid #2b8a3e !important;
    box-shadow: 0 2px 6px rgba(15,23,42,.15); }
[data-testid="stTabs"] [role="tab"][aria-selected="true"] p { font-weight: 600; }
/* light blue export buttons */
[class*="st-key-dl_"] button { background-color: #dbeafe !important; border: 1px solid #93c5fd !important; color: #1e3a8a !important; }
[class*="st-key-dl_"] button p { color: #1e3a8a !important; }
[class*="st-key-dl_"] button:hover { background-color: #bfdbfe !important; border-color: #60a5fa !important; }
[class*="st-key-dl_"] button:active { background-color: #93c5fd !important; }
</style>""", unsafe_allow_html=True)
    _job0 = nest_jobs().get(pid)
    run = st.button("▶ Nest", key="nest_btn", type="primary", disabled=bool(_job0 and _job0.state == "running"))
    if _pending and _pending[0] == "nest":
        run = True                                   # confirmed in the dialog
    elif run and unsaved_moves():
        unsaved_dialog("nest")                       # would replace the moved layout: ask first
        run = False

if save_clicked:  # sidebar Save: sheet/nesting settings first (small and safe), then the parts
    snap = settings_snapshot()
    try:
        db.save_settings(pid, snap)
        settings_msg = (f"sheet {snap['sheet_w']:g} × {snap['sheet_h']:g}, margin {snap['margin']:g}, "
                        f"kerf {snap['spacing']:g}, rotation {snap['rot_label']}, precision {snap['prec_label']}, "
                        f"effort {snap['effort_label']}")
    except Exception as e:  # noqa: BLE001
        settings_msg = None
        st.error(f"Could not save the settings: {e}")
    try:
        st.session_state.parts = current
        db.save_parts(pid, current)
        parts_ok = True
    except Exception as e:  # noqa: BLE001
        parts_ok = False
        st.error(f"Could not save the parts: {e}")
    if settings_msg and parts_ok:
        st.toast(f"Saved “{proj['name']}”: {len(current)} parts · {settings_msg}", icon=":material/check_circle:")
    elif settings_msg:
        st.toast(f"Settings saved ({settings_msg}), but the parts were NOT saved.", icon=":material/warning:")


# ---------- nesting ----------
@st.fragment(run_every=1.5)
def nest_progress(job_id):
    """Progress of the background nesting; ends with a full re-run when the job has finished."""
    job = nest_jobs().get(pid)
    if job is None or job.id != job_id or job.state != "running":
        st.rerun()
        return
    st.progress(job.frac, text=f"Nesting… {job.msg}")
    st.caption(f"{fmt_time(time.time() - job.started)} so far. Nesting keeps running on the server: you can lock your phone "
               "or leave the page and come back later; the result is saved in the History.")
    if st.button("Cancel nesting", key="cancel_nest"):
        job.cancel = True


with tab_table:
    if run:
        to_nest = [p for p in current if p["qty"] > 0]
        if not to_nest:
            st.warning("Nothing to nest: add a part with a quantity of at least 1.")
        elif _job0 is not None and _job0.state == "running":
            st.warning("A nesting is already running for this project.")
        elif all(p["shape"] in ("rectangle",) and stored_geometry(p.get("outline")) is None for p in to_nest):
            t_start = time.perf_counter()           # rectangles only: exact and instant, no background job needed
            sheets = nest([Part(p["name"], p["width"], p["height"], p["qty"], p["rotate"]) for p in to_nest],
                          sheet_w, sheet_h, margin, spacing)
            elapsed = time.perf_counter() - t_start
            s = summary(sheets)
            result = [{"width": sh.width, "height": sh.height,
                       "placements": [pl.__dict__ for pl in sh.placements]} for sh in sheets]
            db.save_parts(pid, current)
            run_id = db.save_run(pid, sheet_w, sheet_h, margin, spacing, s["sheets"], s["utilization"], result, elapsed,
                                 options=nest_options())
            st.session_state.parts = current
            st.session_state.nest_info = {}
            set_result(result, run_id)
            st.session_state.nest_seconds = elapsed
            st.session_state.nest_parts = [{k: p[k] for k in ("name", "shape", "width", "height", "qty")} for p in to_nest]
            st.session_state.run_meta = {"margin": margin, "spacing": spacing}
        else:
            # irregular parts can take minutes: nest in a background thread so that locking the phone or losing the
            # connection does not stop it; the thread saves the finished run to the History itself
            st.session_state.parts = current
            snap = dict(sheet_w=sheet_w, sheet_h=sheet_h, margin=margin, spacing=spacing)
            cfg = dict(snap, rot_step=ROT_STEPS[rot_label], cell=max(sheet_w, sheet_h) / PRECISION[prec_label],
                       time_limit=EFFORT[effort_label])
            job = NestJob(pid, [{k: p[k] for k in ("name", "shape", "width", "height", "qty")} for p in to_nest],
                          {"margin": margin, "spacing": spacing})
            parts_to_save = [dict(p) for p in current]
            job_pid = pid
            job_options = nest_options()

            def _save(result, s, elapsed):
                db.save_parts(job_pid, parts_to_save)
                return db.save_run(job_pid, snap["sheet_w"], snap["sheet_h"], snap["margin"], snap["spacing"],
                                   s["sheets"], s["utilization"], result, elapsed, options=job_options)

            nest_jobs()[pid] = job
            start_job(job, to_nest, cfg, _save)
            st.rerun()                                # shows the progress and disables the Nest button

    # a background nesting that finished (also one that finished while this page was closed or the phone was locked)
    _job = nest_jobs().get(pid)
    if _job is not None and _job.state == "done" and st.session_state.get("adopted_job") != _job.id:
        st.session_state.adopted_job = _job.id
        if unsaved_moves():
            st.info(f"A nesting finished in the background (run #{_job.run_id}). Your unsaved moves were kept: "
                    "open the new run with **Load run** in the History when you are ready.")
        else:
            set_result(_job.result, _job.run_id)
            st.session_state.nest_info = _job.info
            st.session_state.nest_seconds = _job.elapsed
            st.session_state.nest_parts = _job.nest_parts
            st.session_state.run_meta = _job.meta
            st.rerun()                               # the History in the sidebar lists the new run
    elif _job is not None and _job.state in ("error", "cancelled") and st.session_state.get("seen_job") != _job.id:
        st.session_state.seen_job = _job.id
        if _job.state == "error":
            st.error(_job.error)
        else:
            st.info("Nesting cancelled.")
    elif _job is not None and _job.state == "running":
        nest_progress(_job.id)


def _sheets_from(result):
    """Sheet objects of a stored result. Parts that were set aside in the hold area are NOT on the sheet: left out."""
    fields = Placement.__dataclass_fields__
    return [Sheet(r["width"], r["height"],
                  [Placement(**{k: v for k, v in p.items() if k in fields}) for p in r["placements"] if not is_held(p)])
            for r in result]


def held_parts(result) -> list:
    """['Sheet 2: H-104A', ...] for the parts that are in a hold area instead of on their sheet."""
    return [f"Sheet {i}: {p['part']}" for i, r in enumerate(result, 1) for p in r["placements"] if is_held(p)]


with history_box:
    st.subheader("History")
    runs = db.list_runs(pid)
    labels = {f"#{r['id']} · {r['sheets']} sheets · {r['utilization']:.2%}"
              + (f" · {fmt_time(r.get('seconds'))}" if r.get("seconds") is not None else ""): r["id"] for r in runs}
    if "flash" in st.session_state:
        _msg, _icon = st.session_state.pop("flash")
        st.toast(_msg, icon=_icon)
    if labels:
        pick = st.selectbox("Past runs", list(labels))
        b_load, b_del = st.columns(2)
        if b_load.button("Load run", width="stretch"):
            if unsaved_moves():
                unsaved_dialog("load_run", labels[pick])
            else:
                do_load_run(labels[pick])
        if b_del.button("Delete…", icon=":material/delete:", width="stretch"):
            st.session_state.del_runs_sel = [pick]
            delete_runs_dialog(pid, labels)
    else:
        st.caption("No runs yet.")

def scrap_lists(result, margin, cache) -> list:
    """Usable offcut rectangles of every sheet; cached per sheet layout, so an edit only recomputes its own sheet."""
    out = []
    for i, sh in enumerate(_sheets_from(result)):
        k = (i, hash(json.dumps(result[i])), margin)
        if k not in cache:
            if len(cache) > 300:
                cache.clear()
            cache[k] = scrap_rectangles(sh, margin)
        out.append(cache[k])
    return out


def cut_breakdown(sh, number: int) -> None:
    """Folded table: the cut length of every part on a sheet (outline, holes) and the total."""
    with st.expander(f"Cut length by part – sheet {number}"):
        rows = []
        for k, p in enumerate(sh.placements, 1):
            outline, holes, hole_len = p.cut_parts()
            rows.append({"No.": k, "Part": p.part, "Outline": round(outline, 1), "Holes": holes,
                         "Hole outlines": round(hole_len, 1), "Cut length": round(outline + hole_len, 1)})
        st.dataframe(rows, hide_index=True, width="stretch", column_config={
            "No.": st.column_config.NumberColumn(width=45), "Holes": st.column_config.NumberColumn(width=60)})
        st.markdown(f"**Total for sheet {number}: {sh.cut_length:,.1f}**  ·  {len(rows)} parts")


@st.fragment
def sheets_area(meta, scrap_cache):
    """The unsaved-moves banner, the edit toolbar and the sheets. It is a fragment: editing a part re-runs only this
    function, not the whole page, so the page does not grey out and a move feels immediate."""
    result = st.session_state.result
    sheets = _sheets_from(result)
    cidx = part_color_indices(r["placements"] for r in result)       # a part has the same colour on every sheet
    edit = st.session_state.get("edit_mode", False)
    history = st.session_state.get("edit_history", [])
    top = st.container()                      # drawn below, after the sheets have handled the latest edit
    def draw_toolbar():
        history = st.session_state.get("edit_history", [])
        leave_guard(len(history))
        if history:
            n = len(history)
            st.warning(f"⚠ **{n} unsaved move{'s' if n != 1 else ''}.** They are lost if you reload the page, load another "
                       "run, nest again or open another project. Tap **Save changes** (or **Save as new run**) to keep them.",
                       icon=None)
        held = held_parts(result)
        if held:
            names = ", ".join(held)
            if edit:
                st.info(f"📦 **{len(held)} part{'s' if len(held) != 1 else ''} on hold** ({names}). They are below the sheet in the "
                        "hold area and are not on the sheet: drag them back in when there is room.")
            else:
                st.warning(f"⚠ **{len(held)} part{'s' if len(held) != 1 else ''} left outside the sheet** ({names}). They are in the "
                           "hold area, not on the sheet, and not in the exports. Switch on *Edit parts* and drag them back onto the sheet.")
        if edit or history:                                       # the save buttons stay while there is something to save
            tb1, tb2, tb3, tb4 = st.columns(4)
            run_id = st.session_state.get("run_id")
            if tb1.button("↶ Undo last move", disabled=not history, width="stretch"):
                si, part, dx, dy = history.pop()
                if dx == "snapshot":                                  # a rotation / flip: put the old placement back
                    result[si]["placements"][part] = dy
                else:
                    apply_move(result[si], part, -dx, -dy, meta["margin"], meta["spacing"])
                st.session_state.edit_rev[si] = st.session_state.edit_rev.get(si, 0) + RESYNC   # editors drop what they had queued
                st.rerun(scope="fragment")
            if tb2.button("Reset layout", disabled=not history, width="stretch"):
                set_result(copy.deepcopy(st.session_state.result_original), run_id)
                st.rerun()
            if tb3.button("💾 Save changes", type="primary", disabled=not (history and run_id), width="stretch",
                          help="Store the moved layout in this run of the project history."):
                db.update_run_result(pid, run_id, result)
                st.session_state.result_original = copy.deepcopy(result)
                st.session_state.edit_history = []
                st.session_state.flash = ("Layout saved to the run.", ":material/check_circle:")
                st.rerun()
            if tb4.button("➕ Save as new run", disabled=not history, width="stretch",
                          help="Keep the original run as it is and store the moved layout as a new run."):
                nid = save_as_new_run()
                st.session_state.flash = (f"Saved as new run #{nid}.", ":material/check_circle:")
                st.rerun()
            if edit and not history:
                st.caption("No moves yet. Every sheet below can be edited.")
    for i, sh in enumerate(sheets, 1):
        ekey = f"sheet_editor_{st.session_state.result_token}_{i}"
        if edit:
            # the edits the browser reported since the last run are handled first, so everything drawn below is current
            mv = st.session_state.get(ekey)
            seen = st.session_state.setdefault("applied_moves", set())
            if isinstance(mv, dict) and mv.get("id") not in seen:
                seen.add(mv.get("id"))
                apply_edits(i - 1, mv.get("ops") or [], meta, history)
        scrap = scrap_lists_for(result, i - 1, scrap_cache)
        best = scrap[0] if scrap else None
        title = (f"Sheet {i} – {sh.utilization:.2%} used · cut length {sh.cut_length:,.0f}"
                 + (f" · largest offcut {best[2]:.0f} × {best[3]:.0f}" if best else ""))
        if not edit:
            st.pyplot(draw_sheet(sh, f"Sheet {i}", scrap, color_index=cidx))
            cut_breakdown(sh, i)
            continue
        st.markdown(f"**{title}**")
        cut_breakdown(sh, i)
        ensure_polys(result[i - 1])
        sheet_editor(result[i - 1], meta["margin"], meta["spacing"], scrap, st.session_state.edit_rev.get(i - 1, 0), key=ekey, color_index=cidx)

    with top:
        draw_toolbar()

def scrap_lists_for(result, k, cache):
    sh = _sheets_from([result[k]])[0]
    key = (k, hash(json.dumps(result[k])), margin)
    if key not in cache:
        if len(cache) > 300:
            cache.clear()
        cache[key] = scrap_rectangles(sh, margin)
    return cache[key]


RESYNC = 100000   # added to a sheet's edit counter when the editor must drop its queued edits (undo, refused edit)


def apply_edits(si: int, ops: list, meta: dict, history: list) -> bool:
    """Apply the edits the browser reports for sheet `si`, in order. Each op carries a sequence number; the sheet
    remembers how many it has handled (edit_rev), so a resent or out-of-order op is never applied twice.
    Returns True when an edit was refused (the editor is then re-synchronised by the next draw)."""
    sh_i = st.session_state.result[si]
    expected = st.session_state.edit_rev.get(si, 0)
    for o in sorted((o for o in ops if isinstance(o, dict) and isinstance(o.get("seq"), int)), key=lambda o: o["seq"]):
        if o["seq"] < expected:
            continue                                              # already handled
        if o["seq"] > expected:
            break                                                 # a gap: wait for the earlier ones
        try:
            k = int(o["part"])
            if o.get("kind") in ("rotate", "flip"):
                before = copy.deepcopy(sh_i["placements"][k]) if 0 <= k < len(sh_i["placements"]) else None
                ok, msg = apply_transform(sh_i, k, o["kind"], o.get("value"), 0.0, 0.0, float(o.get("nx", 0)),
                                          float(o.get("ny", 0)), meta["margin"], meta["spacing"])
                entry = (si, k, "snapshot", before)
            else:
                ok, msg = apply_move(sh_i, k, float(o["dx"]), float(o["dy"]), meta["margin"], meta["spacing"])
                entry = (si, k, float(o["dx"]), float(o["dy"]))
        except (KeyError, TypeError, ValueError):
            ok, msg = False, "invalid request"
        if ok:
            history.append(entry)
            st.session_state.edit_history = history
            expected += 1
            st.session_state.edit_rev[si] = expected
        else:
            st.toast(f"That change is not allowed: {msg}.", icon=":material/block:")
            st.session_state.edit_rev[si] = expected + RESYNC
            return True
    return False


# ---------- results: shown on the first tab only ----------
with tab_table:
    if "result" in st.session_state:
        sheets = _sheets_from(st.session_state.result)
        s = summary(sheets)
        info = st.session_state.get("nest_info")

        # 1. headline numbers, including how long the nest took
        _rid_shown = st.session_state.get("run_id")                      # the run that is on the screen (loaded or just nested)
        st.subheader(f"Run {_rid_shown}" if _rid_shown else "Run (not saved)")
        with st.container(key="metrics_row"):
            m1, m2, m3, m4, m5 = st.columns(5)
            m1.metric("Sheets", s["sheets"])
            m2.metric("Parts placed", s["parts"])
            m3.metric("Utilization", f"{s['utilization']:.2%}")
            m4.metric("Time taken", fmt_time(st.session_state.get("nest_seconds")))
            m5.metric("Cut length", f"{s['cut_length']:,.0f}")
        if info:
            st.caption(f"Best of {info['runs']} layouts "
                       f"({'paired parts' if info['set'] == 'paired' else 'single parts'}, placement rule: {info['rule']}"
                       f"{', scrap-aware' if info.get('protect') else ''}). Green dashed boxes = usable scrap.")

        st.caption("Cut length = the border of all the parts (every outline and hole); it is also shown in each sheet's title. "
                   "Where two parts touch, the shared edge counts once for each part.")

        # 2. export, right above the nest. The files are built when you click (deferred), from the layout as it is
        #    at that moment, so hand edits are always included and every edit stays cheap.
        st.subheader("Export")
        _held_now = held_parts(st.session_state.result)
        if _held_now:
            st.warning(f"⚠ {len(_held_now)} part(s) are outside the sheet (hold area) and are NOT in the exports: {', '.join(_held_now)}")
        with_names = st.checkbox("Include part names as text (layer LABELS)", value=True)
        meta = st.session_state.get("run_meta") or {"margin": margin, "spacing": spacing}
        result_ref, scrap_cache = st.session_state.result, st.session_state.setdefault("scrap_cache", {})
        seconds, nest_parts = st.session_state.get("nest_seconds"), st.session_state.get("nest_parts")
        pname = proj["name"]
        step_touch = meta["spacing"] + 3.0               # parts closer than this count as touching: they get different colours
        safe = re.sub(r"[^A-Za-z0-9_-]+", "_", pname).strip("_") or "nest"
        run_no = st.session_state.get("run_id")
        file_base = f"{safe}_run{run_no}" if run_no else safe     # every downloaded file starts with the project name and the run number

        def _sheets_now():
            return _sheets_from(result_ref)

        def _xlsx():
            return cutlist_xlsx(_sheets_now(), project=pname, margin=meta["margin"], spacing=meta["spacing"],
                                seconds=seconds, parts=nest_parts, offcuts=scrap_lists(result_ref, margin, scrap_cache))

        d = st.columns(4)
        if len(sheets) == 1:
            d[0].download_button("⬇ DXF", lambda: sheet_dxf(_sheets_now()[0], with_names), f"{file_base}_sheet_01.dxf",
                                 "application/dxf", width="stretch", key="dl_dxf", on_click="ignore")
        else:                                                 # one button that opens the two DXF options
            with d[0].popover("⬇ DXF", width="stretch", key="dl_pop_dxf"):
                st.download_button(f"{len(sheets)} files (ZIP)", lambda: sheets_zip(_sheets_now(), prefix=f"{file_base}_sheet", labels=with_names),
                                   f"{file_base}_sheets_dxf.zip", "application/zip", width="stretch", key="dl_dxf_zip", on_click="ignore")
                st.download_button("All sheets in one file (stacked)", lambda: all_sheets_dxf(_sheets_now(), labels=with_names),
                                   f"{file_base}_all_sheets.dxf", "application/dxf", width="stretch", key="dl_dxf_all", on_click="ignore")
        with d[1].popover("⬇ STEP", width="stretch", key="dl_pop_step"):      # the STEP options and the part thickness
            step_t = st.number_input("STEP thickness (0 = flat faces, otherwise solid plates)", min_value=0.0, value=0.0,
                                     step=0.5, key="step_thickness", help="0 exports every part as a flat face; a thickness "
                                     "(e.g. 3) exports solid plates of that thickness. Part names are kept in the file. "
                                     "Press Enter after typing a number.")
            # The download starts the moment the button is clicked, possibly before a number that was just typed has been
            # applied by the page. The file therefore reads the thickness when it is built, after a short pause during
            # which the page applies it (the box is also read again on every run).
            holder = st.session_state.setdefault("_step_t_holder", {"t": 0.0})
            holder["t"] = float(step_t)

            def _thickness():
                time.sleep(0.8)
                return holder["t"]

            tag = f" · {step_t:g} thick" if step_t else " · flat 2D"
            if len(sheets) == 1:
                st.download_button("STEP file" + tag, lambda: sheet_step(_sheets_now()[0], 1, _thickness(), step_touch), f"{file_base}_sheet_01.step",
                                   "application/step", width="stretch", key="dl_step", on_click="ignore")
            else:
                st.download_button(f"{len(sheets)} files (ZIP)" + tag, lambda: sheets_step_zip(_sheets_now(), prefix=f"{file_base}_sheet", thickness=_thickness(), touch=step_touch),
                                   f"{file_base}_sheets_step.zip", "application/zip", width="stretch", key="dl_step_zip", on_click="ignore")
                st.download_button("All sheets in one file (stacked)" + tag, lambda: all_sheets_step(_sheets_now(), thickness=_thickness(), touch=step_touch),
                                   f"{file_base}_all_sheets.step", "application/step", width="stretch", key="dl_step_all", on_click="ignore")
        d[2].download_button("⬇ Cut list (Excel)", _xlsx, f"{file_base}_cutlist.xlsx",
                             "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", width="stretch",
                             key="dl_xlsx", on_click="ignore")
        d[3].download_button("⬇ Layout (JSON)", lambda: json.dumps(result_ref), f"{file_base}_layout.json", width="stretch",
                             key="dl_json", on_click="ignore")

        # 3. the sheets
        edit = st.toggle("✋ Edit parts: move, rotate, flip", key="edit_mode",
                         help="Click a part to get its menu. Drag it (or use the arrow keys) to move it; Rotate and Flip turn it in place when it has room. "
                              "A part moves freely in the empty space "
                              "around it and stops when it touches a neighbouring part or the sheet edge.")
        was_editing = st.session_state.get("_edit_prev", False)
        st.session_state._edit_prev = bool(edit)
        _held = held_parts(st.session_state.result)
        if was_editing and not edit and _held:
            st.toast(f"{len(_held)} part(s) are still outside the sheet: {', '.join(_held)}", icon=":material/warning:")
        sheets_area(meta, scrap_cache)
    else:
        leave_guard(0)


# NestoCAM is drawn last: it must see the run that was nested or loaded earlier in this same run of the script
# (Load run, a finished nesting ...); a tab only shows what the script had when it was drawn.
with tab_camming:
    _rid = st.session_state.get("run_id")
    _safe_name = re.sub(r"[^A-Za-z0-9_-]+", "_", proj["name"]).strip("_") or "nest"
    camming_tab(db, pid, st.session_state.get("result"), proj["name"], st.session_state.get("run_meta") or {"margin": margin, "spacing": spacing},
                _rid, f"{_safe_name}_run{_rid}" if _rid else _safe_name)
