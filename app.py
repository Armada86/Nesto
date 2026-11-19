import json

import pandas as pd
import streamlit as st

import db
from dxf_io import read_dxf
from nesting import Part, Sheet, Placement, nest, summary
from render import draw_sheet

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

# ---------- parts ----------
if st.session_state.get("loaded_pid") != pid:
    rows = db.load_parts(pid)
    st.session_state.parts = rows
    st.session_state.loaded_pid = pid
parts_state: list[dict] = st.session_state.parts

st.subheader("Parts")
tab_table, tab_dxf = st.tabs(["Enter dimensions", "Import DXF drawings"])

with tab_table:
    df = pd.DataFrame(
        [{k: p[k] for k in ("name", "width", "height", "qty", "rotate")} for p in parts_state]
        or [{"name": "", "width": 0.0, "height": 0.0, "qty": 1, "rotate": True}]
    )
    edited = st.data_editor(
        df, num_rows="dynamic", use_container_width=True, key=f"editor_{pid}_{len(parts_state)}",
        column_config={
            "name": "Name", "width": st.column_config.NumberColumn("Width", min_value=0.0),
            "height": st.column_config.NumberColumn("Height", min_value=0.0),
            "qty": st.column_config.NumberColumn("Qty", min_value=1, step=1),
            "rotate": st.column_config.CheckboxColumn("Can rotate 90°"),
        },
    )
    outlines = {p["name"]: p.get("outline") for p in parts_state}
    current = [
        {"name": str(r["name"]) or f"part{i+1}", "width": float(r["width"]), "height": float(r["height"]),
         "qty": int(r["qty"]), "rotate": bool(r["rotate"]), "outline": outlines.get(str(r["name"]))}
        for i, r in edited.iterrows() if r["width"] and r["height"] and r["width"] > 0 and r["height"] > 0
    ]

with tab_dxf:
    st.caption("One part per DXF file (LINE/ARC/CIRCLE/POLYLINE/SPLINE). Part size = drawing bounding box.")
    files = st.file_uploader("DXF files", type=["dxf"], accept_multiple_files=True)
    dxf_qty = st.number_input("Quantity for imported files", min_value=1, value=1)
    if files and st.button("Add DXF parts"):
        added = 0
        for f in files:
            try:
                w, h, outline = read_dxf(f.getvalue())
                parts_state.append({"name": f.name.rsplit(".", 1)[0], "width": round(w, 3), "height": round(h, 3),
                                    "qty": int(dxf_qty), "rotate": True, "outline": outline})
                added += 1
            except ValueError as e:
                st.error(f"{f.name}: {e}")
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
if run:
    if not current:
        st.warning("Add at least one part.")
    else:
        try:
            sheets = nest([Part(p["name"], p["width"], p["height"], p["qty"], p["rotate"], p.get("outline"))
                           for p in current], sheet_w, sheet_h, margin, spacing)
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
    return [Sheet(r["width"], r["height"], [Placement(**p) for p in r["placements"]]) for r in result]

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
    m1.metric("Sheets", s["sheets"]); m2.metric("Parts placed", s["parts"]); m3.metric("Utilization", f"{s['utilization']:.1%}")
    for i, sh in enumerate(sheets, 1):
        st.pyplot(draw_sheet(sh, f"Sheet {i}"))
    st.download_button("Download layout (JSON)", json.dumps(st.session_state.result), "nest.json")
