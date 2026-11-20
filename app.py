import json

import pandas as pd
import streamlit as st

import db
from dxf_export import all_sheets_dxf, sheet_dxf, sheets_zip
from dxf_io import read_dxf
from nesting import Part, Placement, Sheet, nest, summary
from nesting_poly import PolyPart, free_rectangles, nest_polygons
from render import draw_sheet
from step_io import read_step
from shapes import SHAPES, geom_to_json, part_geometry, stored_geometry

st.set_page_config(page_title="Nesto – 2D Nesting", layout="wide", page_icon="🧩")

# ---------- database ----------
if not db.get_url():
    st.error("Set DATABASE_URL (your Neon connection string) in `.streamlit/secrets.toml` or as an env var.")
    st.stop()
try:
    db.init_db()
except Exception as e:  # noqa: BLE001
    st.error(f"Cannot connect to Neon: {e}")
    st.stop()

ROT_STEPS = {"None": 0, "90°": 90, "45°": 45, "30°": 30, "15°": 15}
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
    for k in ("result", "nest_info", "parts", "loaded_pid"):
        st.session_state.pop(k, None)
    st.session_state.pid = pid
    if pid is None:
        st.query_params.clear()
        st.session_state.pending_settings = {}
        return
    st.session_state.pending_settings = (db.get_project(pid) or {}).get("settings") or {}
    st.query_params["project"] = str(pid)


@st.dialog("New project")
def new_project_dialog():
    name = st.text_input("Project name", placeholder="e.g. Job 1042 – brackets")
    if st.button("Create project", type="primary", width="stretch"):
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
    if st.button("Open project", type="primary", width="stretch"):
        open_project(labels[choice])
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
    st.markdown("## 🧩 Nesto")
    st.caption("2D nesting")
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
    if r2b.button("Delete", icon=":material/delete:", width="stretch", disabled=not proj):
        delete_project_dialog(pid, proj["name"])

if not proj:
    st.title("Nesto – 2D nesting")
    st.info("Start a new project with **New**, or open an existing one with **Load** (left menu).")
    st.stop()

st.title(f"Nesto – {proj['name']}")

with st.sidebar:
    st.divider()
    st.subheader("Sheet")
    sheet_w = st.number_input("Width", min_value=1.0, step=10.0, key="sheet_w")
    sheet_h = st.number_input("Height", min_value=1.0, step=10.0, key="sheet_h")
    margin = st.number_input("Edge margin", min_value=0.0, step=1.0, key="margin")
    spacing = st.number_input("Part spacing (kerf)", min_value=0.0, step=0.5, key="spacing")

    st.subheader("Nesting")
    rot_label = st.select_slider("Rotation step (irregular shapes)", list(ROT_STEPS), key="rot_label",
                                 help="Finer steps pack irregular shapes better but take longer.")
    prec_label = st.select_slider("Precision (irregular shapes)", list(PRECISION), key="prec_label",
                                  help="Finer = tighter packing, slower.")
    effort_label = st.select_slider("Search effort (irregular shapes)", list(EFFORT), key="effort_label",
                                    help="Quick = one pass. Normal / Thorough keep trying many different layouts "
                                         "(pairing, placement rules, random restarts) for 15 / 60 seconds and keep the best.")

# ---------- parts ----------
if st.session_state.get("loaded_pid") != pid:
    st.session_state.parts = db.load_parts(pid)
    st.session_state.loaded_pid = pid
parts_state: list[dict] = st.session_state.parts

st.subheader("Parts")
tab_table, tab_dxf = st.tabs(["Enter dimensions", "Import DXF / STEP"])

with tab_table:
    st.caption("Size A = width (or diameter for a circle). Size B = height (ignored for circles). "
               "Triangle: A = base, B = height. Parts from DXF keep their drawing; their sizes are informational.")
    cols = ["name", "shape", "width", "height", "qty", "rotate"]
    df = pd.DataFrame(
        [{k: p.get(k, "rectangle" if k == "shape" else None) for k in cols} for p in parts_state]
        or [{"name": "", "shape": "rectangle", "width": 0.0, "height": 0.0, "qty": 1, "rotate": True}]
    )
    edited = st.data_editor(
        df, num_rows="dynamic", width="stretch", key=f"editor_{pid}_{len(parts_state)}",
        column_config={
            "name": "Name",
            "shape": st.column_config.SelectboxColumn("Shape", options=SHAPES, default="rectangle", required=True),
            "width": st.column_config.NumberColumn("Size A", min_value=0.0),
            "height": st.column_config.NumberColumn("Size B", min_value=0.0),
            "qty": st.column_config.NumberColumn("Qty", min_value=1, step=1),
            "rotate": st.column_config.CheckboxColumn("Can rotate"),
        },
    )
    outlines = {p["name"]: p.get("outline") for p in parts_state}
    current = []
    for i, r in edited.iterrows():
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
                        "rotate": bool(r["rotate"]), "outline": outlines.get(nm)})

with tab_dxf:
    st.caption("**DXF**: any closed outline made of LINE / ARC / CIRCLE / POLYLINE / SPLINE / ELLIPSE entities; "
               "inner closed contours become holes (other parts can nest inside them). "
               "**STEP**: flat (plate-like) parts - the largest flat face gives the outline and holes, "
               "any orientation, thickness ignored. One part per solid. Bent parts: export the flat pattern as DXF.")
    files = st.file_uploader("DXF / STEP files", type=["dxf", "step", "stp"], accept_multiple_files=True)
    c_a, c_b, c_c = st.columns(3)
    dxf_qty = c_a.number_input("Quantity per part", min_value=1, value=1)
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
                                    "height": round(y1 - y0, 3), "qty": int(dxf_qty), "rotate": True,
                                    "outline": {"polys": geom_to_json(g)}})
                added += 1
        if added:
            st.session_state.parts = parts_state
            db.save_parts(pid, parts_state)
            st.rerun()

run = st.button("▶ Nest", type="primary")

if save_clicked:  # sidebar Save: parts + sheet/nesting settings of the open project
    st.session_state.parts = current
    db.save_parts(pid, current)
    db.save_settings(pid, {k: st.session_state[k] for k in DEFAULTS})
    st.toast(f"Saved “{proj['name']}” – {len(current)} parts and settings", icon=":material/check_circle:")


# ---------- nesting ----------
def run_nesting(parts):
    all_rect = all(p["shape"] in ("rectangle",) and stored_geometry(p.get("outline")) is None for p in parts)
    if all_rect:  # exact and instant
        return nest([Part(p["name"], p["width"], p["height"], p["qty"], p["rotate"]) for p in parts],
                    sheet_w, sheet_h, margin, spacing)
    bar = st.progress(0.0, text="Nesting…")
    pp = [PolyPart(p["name"], part_geometry(p), p["qty"], p["rotate"]) for p in parts]
    cell = max(sheet_w, sheet_h) / PRECISION[prec_label]
    info = {}
    out = nest_polygons(pp, sheet_w, sheet_h, margin, spacing, ROT_STEPS[rot_label], cell,
                        progress=lambda f, msg: bar.progress(min(max(f, 0.0), 1.0), text=f"Nesting… {msg}"),
                        time_limit=EFFORT[effort_label], info=info)
    bar.empty()
    st.session_state.nest_info = info
    return out


if run:
    if not current:
        st.warning("Add at least one part.")
    else:
        try:
            sheets = run_nesting(current)
        except ValueError as e:
            st.error(str(e))
            st.stop()
        s = summary(sheets)
        result = [{"width": sh.width, "height": sh.height,
                   "placements": [pl.__dict__ for pl in sh.placements]} for sh in sheets]
        db.save_parts(pid, current)
        db.save_run(pid, sheet_w, sheet_h, margin, spacing, s["sheets"], s["utilization"], result)
        st.session_state.parts = current
        st.session_state.result = result


def _sheets_from(result):
    fields = Placement.__dataclass_fields__
    return [Sheet(r["width"], r["height"],
                  [Placement(**{k: v for k, v in p.items() if k in fields}) for p in r["placements"]])
            for r in result]


with st.sidebar:
    st.divider()
    st.subheader("History")
    runs = db.list_runs(pid)
    labels = {f"#{r['id']} · {r['sheets']} sheets · {r['utilization']:.0%}": r["id"] for r in runs}
    if labels:
        pick = st.selectbox("Past runs", list(labels))
        if st.button("Load run"):
            st.session_state.result = db.load_run(labels[pick])["result"]

if "result" in st.session_state:
    sheets = _sheets_from(st.session_state.result)
    s = summary(sheets)
    info = st.session_state.get("nest_info")
    if info:
        st.caption(f"Best of {info['runs']} layouts in {info['seconds']:.0f}s "
                   f"({'paired parts' if info['set'] == 'paired' else 'single parts'}, placement rule: {info['rule']}"
                   f"{', scrap-aware' if info.get('protect') else ''}). Green dashed boxes = usable scrap.")
    m1, m2, m3 = st.columns(3)
    m1.metric("Sheets", s["sheets"])
    m2.metric("Parts placed", s["parts"])
    m3.metric("Utilization", f"{s['utilization']:.1%}")
    key = (hash(json.dumps(st.session_state.result)), margin)  # scrap analysis is cached per result
    if st.session_state.get("scrap_key") != key:
        st.session_state.scrap_key = key
        st.session_state.scrap = [free_rectangles(sh, margin) for sh in sheets]
    for i, sh in enumerate(sheets, 1):
        st.pyplot(draw_sheet(sh, f"Sheet {i}", st.session_state.scrap[i - 1]))
    st.subheader("Export")
    labels = st.checkbox("Include part names as text (layer LABELS)", value=True)
    d1, d2, d3 = st.columns(3)
    if len(sheets) == 1:
        d1.download_button("⬇ DXF (sheet 1)", sheet_dxf(sheets[0], labels), "sheet_01.dxf", "application/dxf")
    else:
        d1.download_button(f"⬇ DXF – {len(sheets)} files (ZIP)", sheets_zip(sheets, labels=labels),
                           "nest_sheets.zip", "application/zip")
        d2.download_button("⬇ DXF – all sheets in one file (stacked)", all_sheets_dxf(sheets, labels=labels),
                           "nest_all_sheets.dxf", "application/dxf")
    d3.download_button("⬇ Layout (JSON)", json.dumps(st.session_state.result), "nest.json")
