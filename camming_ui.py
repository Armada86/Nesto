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
ZOOM = 1.0          # size of the isometric drawing inside its frame (the empty frame is cropped away afterwards; larger would clip)
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


def _crop_to_content(png: bytes, pad: int = 12) -> bytes:
    """The picture without the empty frame around the drawing."""
    import numpy as np
    from PIL import Image

    im = Image.open(io.BytesIO(png)).convert("RGB")
    a = np.asarray(im).astype(int)
    ys, xs = np.nonzero(np.abs(a - 255).sum(axis=2) > 12)
    if len(xs) == 0:
        return png
    box = (max(0, xs.min() - pad), max(0, ys.min() - pad), min(im.width, xs.max() + pad), min(im.height, ys.max() + pad))
    out = io.BytesIO()
    im.crop(box).save(out, format="PNG")
    return out.getvalue()


def solids(sheet: dict) -> tuple:
    """The parts of a sheet as ((outline ring, (hole rings...)), ...): what the isometric picture extrudes to the thickness."""
    out = []
    for p in sheet["placements"]:
        if is_held(p):
            continue
        g = placement_geom(p)
        for poly in ([g] if g.geom_type == "Polygon" else [x for x in g.geoms if x.geom_type == "Polygon"]):
            out.append((tuple(map(tuple, poly.exterior.coords)), tuple(tuple(map(tuple, h.coords)) for h in poly.interiors)))
    return tuple(out)


def height_scale(width: float, height: float, thickness: float) -> float:
    """How much taller than true the stock is drawn: a sheet that is thin compared with its size would be a line."""
    return max(1.0, min(40.0, 0.03 * max(width, height) / max(thickness, 1e-6)))


@st.cache_data(max_entries=12, show_spinner=False)
def isometric_png(width: float, height: float, thickness: float, parts: tuple) -> bytes:
    return _crop_to_content(isometric_raw_png(width, height, thickness, parts))


def isometric_raw_png(width: float, height: float, thickness: float, parts: tuple) -> bytes:
    """The whole sheet in an isometric view, seen from the origin corner (X to the right-back, Y to the left-back, Z up, like
    a CAD isometric view): the stock slab as thin edges and every part as a solid of its height, with its bottom contour
    (the geometry) in purple and the coordinate system at the origin, the lower left corner of the sheet in plan view."""
    from matplotlib.patches import PathPatch
    from matplotlib.path import Path
    from mpl_toolkits.mplot3d import art3d
    from shapely.geometry import Polygon
    from shapely.geometry.polygon import orient

    k = height_scale(width, height, thickness)
    z = thickness * k
    fig = plt.figure(figsize=(10, 5), dpi=170)
    ax = fig.add_subplot(111, projection="3d")
    slab = [[(0, 0, 0), (width, 0, 0), (width, 0, z), (0, 0, z)], [(width, 0, 0), (width, height, 0), (width, height, z), (width, 0, z)],
            [(0, 0, z), (width, 0, z), (width, height, z), (0, height, z)]]
    ax.add_collection3d(art3d.Poly3DCollection(slab, facecolor="#cbd5e1", edgecolor="none", alpha=0.35))   # the stock, light grey
    for zz in (0, z):                                        # the stock slab
        ax.plot([0, width, width, 0, 0], [0, 0, height, height, 0], [zz] * 5, color="#475569", lw=0.6)
    for x, y in ((0, 0), (width, 0), (width, height), (0, height)):
        ax.plot([x, x], [y, y], [0, z], color="#475569", lw=0.6)
    for ext, holes in parts:
        sides = []
        for ring in (ext, *holes):
            sides += [[(a[0], a[1], 0), (b[0], b[1], 0), (b[0], b[1], z), (a[0], a[1], z)] for a, b in zip(ring, ring[1:])]
        ax.add_collection3d(art3d.Poly3DCollection(sides, facecolor="#a78bfa", edgecolor="none", alpha=1.0))
        poly = orient(Polygon(ext, holes), 1.0)               # outline counter-clockwise, holes clockwise: the holes stay open
        verts, codes = [], []
        for ring in (poly.exterior, *poly.interiors):
            c = list(ring.coords)
            verts += c
            codes += [Path.MOVETO] + [Path.LINETO] * (len(c) - 2) + [Path.CLOSEPOLY]
        patch = PathPatch(Path(verts, codes), facecolor="#ddd6fe", edgecolor="#6d28d9", lw=0.5)
        ax.add_patch(patch)
        art3d.pathpatch_2d_to_3d(patch, z=z, zdir="z")
        for ring in (ext, *holes):                           # the geometry: the bottom contour
            xs, ys = zip(*ring)
            ax.plot(xs, ys, [0] * len(xs), color=PURPLE, lw=1.2)
    a = 0.09 * max(width, height)                            # the coordinate system at the origin
    for vec, col, lab in (((a, 0, 0), "#dc2626", "X"), ((0, a, 0), "#16a34a", "Y"), ((0, 0, a), "#2563eb", "Z")):
        ax.plot([0, vec[0]], [0, vec[1]], [0, vec[2]], color=col, lw=2)
        ax.text(vec[0] * 1.15, vec[1] * 1.15, vec[2] * 1.15, lab, color=col, fontsize=9, fontweight="bold")
    ax.text(width / 2, 0, 0, f"{width:g}", fontsize=7, color="#475569", ha="center", va="top")
    ax.text(width, height / 2, 0, f"{height:g}", fontsize=7, color="#475569", ha="left", va="center")
    ax.set_xlim(0, width)
    ax.set_ylim(0, height)
    ax.set_zlim(0, z)
    ax.set_box_aspect((width, height, z), zoom=ZOOM)
    ax.view_init(elev=30, azim=-135)
    ax.set_axis_off()
    fig.subplots_adjust(0, 0, 1, 1)
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
    k = height_scale(float(sheet["width"]), float(sheet["height"]), float(st.session_state.camming_thickness))
    st.image(isometric_png(float(sheet["width"]), float(sheet["height"]), float(st.session_state.camming_thickness), solids(sheet)),
             caption="The whole sheet seen from the origin corner (lower left corner in plan view): X to the right, Y to the back, Z up. "
                     "Every part is a solid of its height; its bottom contour (the geometry) is purple."
                     + (f" The height is drawn {k:.0f} times larger than true." if k > 1.05 else ""))
    st.image(plan_png(float(sheet["width"]), float(sheet["height"]), pts, tuple(c["type"] for c in ch)),
             caption="Plan view: outlines solid, holes dashed. The red dot is the origin.")
    st.dataframe(pd.DataFrame([{"No.": i, "Part": c["part"], "Type": c["type"], "Length": round(c["length"], 1), "Z": 0.0}
                               for i, c in enumerate(ch, 1)]), hide_index=True, width="stretch")


def camming_tab(db, pid, result):
    step = st.radio("Step", [k for k, _ in STEPS], format_func=dict(STEPS).get, horizontal=True, key="camming_step",
                    label_visibility="collapsed")
    if step == "geometry":
        step_geometry(db, pid, result)
