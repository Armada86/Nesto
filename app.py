import json

import pandas as pd
import streamlit as st

import db
from dxf_io import read_dxf
from nesting import Part, Placement, Sheet, nest, summary
from nesting_poly import PolyPart, nest_polygons
from render import draw_sheet
from shapes import SHAPES, geom_to_json, part_geometry, stored_geometry

st.set_page_config(page_title="Nesto – 2D Nesting", layout="wide")
st.title("Nesto – 2D nesting")

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

# ---------- project selection ----------
with st.sidebar:
    st.header("Project")
    projects = db.list_projects()
    names = {p["name"]: p["id"] for p in projects}
    choice = st.selectbox("Open project", ["— new —"] + list(names))
    if choice == "— new —":
        new_name = st.text_input("New project name")
        if st.button("Create") and new_name.strip():
            db.create_project(new_name.strip())
            st.rerun()
        st.info("Create or select a project to start.")
        st.stop()
    pid = names[choice]
    if st.button("Delete project"):
        db.delete_project(pid)
        st.rerun()

    st.header("Sheet")
    sheet_w = st.number_input("Width", min_value=1.0, value=2500.0, step=10.0)
    sheet_h = st.number_input("Height", min_value=1.0, value=1250.0, step=10.0)
    margin = st.number_input("Edge margin", min_value=0.0, value=5.0, step=1.0)
    spacing = st.number_input("Part spacing (kerf)", min_value=0.0, value=3.0, step=0.5)

    st.header("Nesting")
    rot_label = st.select_slider("Rotation step (irregular shapes)", list(ROT_STEPS), value="90°",
                                 help="Finer steps pack irregular shapes better but take longer.")
    prec_label = st.select_slider("Precision (irregular shapes)", list(PRECISION), value="Balanced",
                                  help="Finer = tighter packing, slower.")

# ---------- parts ----------
if st.session_state.get("loaded_pid") != pid:
    st.session_state.parts = db.load_parts(pid)
    st.session_state.loaded_pid = pid
parts_state: list[dict] = st.session_state.parts

st.subheader("Parts")
tab_table, tab_dxf = st.tabs(["Enter dimensions", "Import DXF drawings"])

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
    st.caption("Any closed outline made of LINE / ARC / CIRCLE / POLYLINE / SPLINE / ELLIPSE entities. "
               "Inner closed contours become holes (other parts can nest inside them).")
    files = st.file_uploader("DXF files", type=["dxf"], accept_multiple_files=True)
    c_a, c_b, c_c = st.columns(3)
    dxf_qty = c_a.number_input("Quantity per part", min_value=1, value=1)
    dxf_scale = c_b.number_input("Scale (e.g. 25.4 for inch → mm)", min_value=0.0001, value=1.0, format="%.4f")
    split = c_c.checkbox("One part per closed contour", help="Use for DXFs that contain many separate parts.")
    if files and st.button("Add DXF parts"):
        added = 0
        used = {p["name"] for p in parts_state}
        for f in files:
            try:
                geoms = read_dxf(f.getvalue(), one_part=not split, scale=dxf_scale)
            except ValueError as e:
                st.error(f"{f.name}: {e}")
                continue
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

c1, c2 = st.columns(2)
if c1.button("💾 Save parts"):
    st.session_state.parts = current
    db.save_parts(pid, current)
    st.success(f"Saved {len(current)} parts")
run = c2.button("▶ Nest", type="primary")


# ---------- nesting ----------
def run_nesting(parts):
    all_rect = all(p["shape"] in ("rectangle",) and stored_geometry(p.get("outline")) is None for p in parts)
    if all_rect:  # exact and instant
        return nest([Part(p["name"], p["width"], p["height"], p["qty"], p["rotate"]) for p in parts],
                    sheet_w, sheet_h, margin, spacing)
    bar = st.progress(0.0, text="Nesting…")
    pp = [PolyPart(p["name"], part_geometry(p), p["qty"], p["rotate"]) for p in parts]
    cell = max(sheet_w, sheet_h) / PRECISION[prec_label]
    out = nest_polygons(pp, sheet_w, sheet_h, margin, spacing, ROT_STEPS[rot_label], cell,
                        progress=lambda d, t: bar.progress(d / t, text=f"Nesting… {d}/{t}"))
    bar.empty()
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
    st.header("History")
    runs = db.list_runs(pid)
    labels = {f"#{r['id']} · {r['sheets']} sheets · {r['utilization']:.0%}": r["id"] for r in runs}
    if labels:
        pick = st.selectbox("Past runs", list(labels))
        if st.button("Load run"):
            st.session_state.result = db.load_run(labels[pick])["result"]

if "result" in st.session_state:
    sheets = _sheets_from(st.session_state.result)
    s = summary(sheets)
    m1, m2, m3 = st.columns(3)
    m1.metric("Sheets", s["sheets"])
    m2.metric("Parts placed", s["parts"])
    m3.metric("Utilization", f"{s['utilization']:.1%}")
    for i, sh in enumerate(sheets, 1):
        st.pyplot(draw_sheet(sh, f"Sheet {i}"))
    st.download_button("Download layout (JSON)", json.dumps(st.session_state.result), "nest.json")
