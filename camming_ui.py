"""The "Camming" tab: the CAM process as a list of steps. Step 1 - Geometry: the contours to be cut, taken at the BOTTOM
of the part (Z = 0 is the bottom of the material, as in the work coordinate system of the CAM picture: origin at the
lower left corner of the sheet, X along the sheet length, Y along its width, Z up; the top of the stock is Z = thickness)."""
from __future__ import annotations

import io
import json

import matplotlib
import pandas as pd
import streamlit as st

from layout_edit import is_held, placement_geom

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

PURPLE = "#8b1fc7"
STEPS = [("geometry", "1 · Geometry")]       # the next steps are added here, one by one


def chains(sheet: dict) -> list[dict]:
    """The geometry of a sheet: one closed chain per outline and per hole of every part on it (parts in the hold area are
    not on the sheet). Every chain lies at Z = 0, the bottom of the part."""
    out = []
    for p in sheet["placements"]:
        if is_held(p):
            continue
        g = placement_geom(p)
        for poly in ([g] if g.geom_type == "Polygon" else [x for x in g.geoms if x.geom_type == "Polygon"]):
            out.append({"part": p["part"], "type": "Outline", "pts": [tuple(c) for c in poly.exterior.coords],
                        "length": poly.exterior.length})
            for h in poly.interiors:
                out.append({"part": p["part"], "type": "Hole", "pts": [tuple(c) for c in h.coords], "length": h.length})
    return out


def _png(fig) -> bytes:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight")
    plt.close(fig)
    return buf.getvalue()


@st.cache_data(max_entries=12, show_spinner=False)
def isometric_png(width: float, height: float, thickness: float, chain_pts: tuple) -> bytes:
    """The stock block (thin black edges) with the geometry at its bottom (purple) and the coordinate system at the
    lower left corner, seen like in the CAM picture. The height is drawn larger than true if the stock is very thin."""
    zshow = max(thickness, 0.03 * max(width, height))
    fig = plt.figure(figsize=(8, 4.4), dpi=130)
    ax = fig.add_subplot(111, projection="3d")
    for z in (0, zshow):                                    # the stock block
        ax.plot([0, width, width, 0, 0], [0, 0, height, height, 0], [z] * 5, color="black", lw=0.6)
    for x, y in ((0, 0), (width, 0), (width, height), (0, height)):
        ax.plot([x, x], [y, y], [0, zshow], color="black", lw=0.6)
    for pts in chain_pts:                                   # the geometry: bottom contours of the parts
        xs, ys = zip(*pts)
        ax.plot(xs, ys, [0] * len(xs), color=PURPLE, lw=1.1)
    a = 0.07 * max(width, height)                           # the coordinate system
    for vec, col, lab in (((a, 0, 0), "#dc2626", "X"), ((0, a, 0), "#16a34a", "Y"), ((0, 0, a), "#2563eb", "Z")):
        ax.plot([0, vec[0]], [0, vec[1]], [0, vec[2]], color=col, lw=2)
        ax.text(vec[0], vec[1], vec[2], lab, color=col, fontsize=8)
    ax.set_box_aspect((width, height, zshow), zoom=1.55)
    ax.view_init(elev=30, azim=-58)
    ax.set_axis_off()
    return _png(fig)


@st.cache_data(max_entries=12, show_spinner=False)
def plan_png(width: float, height: float, chain_pts: tuple, kinds: tuple) -> bytes:
    fig, ax = plt.subplots(figsize=(8, 8 * height / width if width else 5), dpi=110)
    ax.add_patch(plt.Rectangle((0, 0), width, height, fill=False, lw=1.2, color="black"))
    for pts, kind in zip(chain_pts, kinds):
        xs, ys = zip(*pts)
        ax.plot(xs, ys, color=PURPLE, lw=1.0, ls="-" if kind == "Outline" else "--")
    ax.plot([0], [0], "o", color="#dc2626", ms=4)
    ax.set_aspect("equal")
    ax.set_xlim(-0.02 * width, width * 1.02)
    ax.set_ylim(-0.02 * height, height * 1.02)
    return _png(fig)


def _saved(db, pid) -> dict:
    try:
        return (db.get_cam(pid) or {}).get("camming", {})
    except Exception:  # noqa: BLE001 - the saved settings are optional
        return {}


def step_geometry(db, pid, result):
    st.caption("The geometry is the **bottom outline of every part**: the outside contour of each part and the contour of each "
               "of its holes, all at **Z = 0, the bottom of the material**. The work coordinate system is at the lower left corner "
               "of the sheet at the bottom (X along the length, Y along the width, Z up); the top of the material is Z = thickness.")
    if st.session_state.get("camming_loaded_pid") != pid:
        st.session_state.camming_thickness = float(_saved(db, pid).get("thickness", 18.0))
        st.session_state.camming_loaded_pid = pid
    c1, c2 = st.columns([1, 2])
    c1.number_input("Material thickness", min_value=0.1, step=0.5, key="camming_thickness",
                    help="Height of the stock: the top of the material is at Z = thickness.")
    if c1.button("Save", key="camming_save_thickness"):
        try:
            cam = db.get_cam(pid) or {}
            cam["camming"] = {**cam.get("camming", {}), "thickness": float(st.session_state.camming_thickness)}
            db.save_cam(pid, cam)
            st.toast("Saved with the project.", icon=":material/check_circle:")
        except Exception as e:  # noqa: BLE001
            st.error(f"Could not save: {e}")
    if not result:
        st.info("Nest first: the geometry is taken from the sheets of the current run.")
        return
    sheet_no = c2.selectbox("Sheet", list(range(1, len(result) + 1)), key="camming_sheet", format_func=lambda i: f"Sheet {i}")
    sheet = result[sheet_no - 1]
    ch = chains(sheet)
    held = [p["part"] for r in result for p in r["placements"] if is_held(p)]
    if held:
        st.warning(f"{len(held)} part(s) are in the hold area and are not part of the geometry: {', '.join(held)}")
    n_out, n_hole = sum(1 for c in ch if c["type"] == "Outline"), sum(1 for c in ch if c["type"] == "Hole")
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Outlines", n_out)
    m2.metric("Holes", n_hole)
    m3.metric("Total length", f"{sum(c['length'] for c in ch):,.0f}")
    m4.metric("Z of the geometry", "0")
    pts = tuple(tuple(c["pts"]) for c in ch)
    st.image(isometric_png(float(sheet["width"]), float(sheet["height"]), float(st.session_state.camming_thickness), pts),
             caption="Stock (black) with the geometry at its bottom (purple) and the coordinate system at the lower left corner. "
                     "The height may be drawn larger than true.")
    st.image(plan_png(float(sheet["width"]), float(sheet["height"]), pts, tuple(c["type"] for c in ch)),
             caption="Plan view: outlines solid, holes dashed. The red dot is the origin.")
    st.dataframe(pd.DataFrame([{"No.": i, "Part": c["part"], "Type": c["type"], "Length": round(c["length"], 1), "Z": 0.0}
                               for i, c in enumerate(ch, 1)]), hide_index=True, width="stretch")


def camming_tab(db, pid, result):
    step = st.radio("Step", [k for k, _ in STEPS], format_func=dict(STEPS).get, horizontal=True, key="camming_step",
                    label_visibility="collapsed")
    if step == "geometry":
        step_geometry(db, pid, result)
