"""The "Camming" tab: the CAM process as a list of steps. Step 1 - Geometry: the contours to be cut, taken at the BOTTOM
of the part (Z = 0 is the bottom of the material, as in the work coordinate system of the CAM picture: origin at the
lower left corner of the sheet, X along the sheet length, Y along its width, Z up; the top of the stock is Z = thickness)."""
from __future__ import annotations

import io
import json

import matplotlib
import pandas as pd
import streamlit as st

import cam
from layout_edit import is_held, placement_geom

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

PURPLE = "#dc2626"   # the colour of the geometry (bottom contour): red, it becomes the toolpath
PICTURE_VERSION = 10  # raise it whenever the drawing code changes: the pictures are cached by their arguments, so without this an
                     # older picture of the same sheet would still be shown after an update
ZOOM = 1.0
FONT_PT = 4.5   # size of the dimension numbers          # size of the isometric drawing inside its frame (the empty frame is cropped away afterwards; larger would clip)


def chains(sheet: dict) -> list[dict]:
    """The geometry of a sheet: one closed chain per outline and per hole of every part on it (parts in the hold area are
    not on the sheet). Every chain lies at Z = 0, the bottom of the part. Each chain has an id (cam.contour_id: the same name the
    toolpaths use), and the point where its label is written."""
    from shapely.geometry import Polygon

    out = []
    for p in sheet["placements"]:
        if is_held(p):
            continue
        g = placement_geom(p)
        for poly in ([g] if g.geom_type == "Polygon" else [x for x in g.geoms if x.geom_type == "Polygon"]):
            b = poly.bounds
            key = (round(b[0], 3), round(b[1], 3))
            lp = poly.representative_point()
            out.append({"part": p["part"], "type": "Outline", "pts": [tuple(c) for c in poly.exterior.coords],
                        "length": poly.exterior.length, "key": key, "id": cam.contour_id("outer", key), "label_pt": (lp.x, lp.y)})
            for h in poly.interiors:
                hp = Polygon(h)
                hb = hp.bounds
                hkey = (round(hb[0], 3), round(hb[1], 3))
                hl = hp.representative_point()
                out.append({"part": p["part"], "type": "Hole", "pts": [tuple(c) for c in h.coords], "length": h.length, "key": hkey,
                            "id": cam.contour_id("inner", hkey), "label_pt": (hl.x, hl.y)})
    return out


def default_order(ch: list[dict]) -> list[str]:
    """The default cutting order: the holes first, then the outlines, each from the lower left corner of its part, left to right."""
    holes = sorted((c for c in ch if c["type"] == "Hole"), key=lambda c: c["key"])
    outs = sorted((c for c in ch if c["type"] == "Outline"), key=lambda c: c["key"])
    return [c["id"] for c in holes + outs]


def full_order(ch: list[dict], saved: list | None) -> list[str]:
    """The cutting order of the contours: the saved order (contours that no longer exist are dropped), then the contours it does
    not mention, in the default order."""
    dflt = default_order(ch)
    if not saved:
        return dflt
    seen, out = set(), []
    for cid in list(saved) + dflt:
        if cid in dflt and cid not in seen:
            seen.add(cid)
            out.append(cid)
    return out


def saved_order(db, pid, sheet_no: int):
    """The order saved in the operation for this sheet (None = the default order)."""
    s = _saved(db, pid)
    return s.get("order") if s.get("order_sheet") == sheet_no else None


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


@st.cache_data(max_entries=12, show_spinner=False)
def isometric_png(width: float, height: float, thickness: float, parts: tuple, k: float = 1.0, version: int = PICTURE_VERSION) -> bytes:
    return _crop_to_content(isometric_raw_png(width, height, thickness, parts, k))


def _touches_border(png: bytes) -> bool:
    import numpy as np
    from PIL import Image

    im = np.asarray(Image.open(io.BytesIO(png)).convert("RGB")).astype(int)
    border = np.concatenate([im[0], im[-1], im[:, 0], im[:, -1]])
    return bool((np.abs(border - 255).sum(axis=1) > 30).any())


def isometric_raw_png(width: float, height: float, thickness: float, parts: tuple, k: float = 1.0) -> bytes:
    """Drawn with a frame that is made larger until nothing (text included) is cut off at its border."""
    room = 1.0
    for _ in range(6):
        png = _isometric(width, height, thickness, parts, k, room)
        if not _touches_border(png):
            return png
        room *= 1.25
    return png


def _isometric(width: float, height: float, thickness: float, parts: tuple, k: float, room: float) -> bytes:
    """The whole sheet in an isometric view, seen from the origin corner (X to the right-back, Y to the left-back, Z up, like
    a CAD isometric view), drawn to scale (k = 1; k > 1 draws the height k times larger): the stock slab, every part as a
    solid of its height with its bottom contour (the geometry) in red, the sheet dimensions outside the sheet and the
    coordinate system at the origin, the lower left corner of the sheet in plan view."""
    from matplotlib.patches import PathPatch
    from matplotlib.path import Path
    from mpl_toolkits.mplot3d import art3d
    from shapely.geometry import Polygon
    from shapely.geometry.polygon import orient

    big = max(width, height)
    z = thickness * k                                        # the drawn height of the slab
    off = 0.07 * big                                         # how far outside the sheet the dimension lines run
    arm = 0.05 * big                                         # length of the coordinate system arrows (small and thin)
    fig = plt.figure(figsize=(11, 5.5), dpi=720)
    ax = fig.add_subplot(111, projection="3d")
    slab = [[(0, 0, 0), (width, 0, 0), (width, 0, z), (0, 0, z)], [(width, 0, 0), (width, height, 0), (width, height, z), (width, 0, z)],
            [(0, 0, z), (width, 0, z), (width, height, z), (0, height, z)]]
    ax.add_collection3d(art3d.Poly3DCollection(slab, facecolor="#cbd5e1", edgecolor="none", alpha=0.35))   # the stock, light grey
    for zz in (0, z):
        ax.plot([0, width, width, 0, 0], [0, 0, height, height, 0], [zz] * 5, color="#475569", lw=0.4)
    for x, y in ((0, 0), (width, 0), (width, height), (0, height)):
        ax.plot([x, x], [y, y], [0, z], color="#475569", lw=0.4)
    for ext, holes in parts:
        for ring in (ext, *holes):                           # transparent part: dotted top outline and dotted vertical edges
            xs, ys = zip(*ring)
            ax.plot(xs, ys, [z] * len(xs), color="#7c3aed", lw=0.4, ls=(0, (2, 4)))
            for x, y in ring[:-1]:
                ax.plot([x, x], [y, y], [0, z], color="#7c3aed", lw=0.35, ls=(0, (2, 4)))
        for ring in (ext, *holes):                           # the geometry: the bottom contour
            xs, ys = zip(*ring)
            ax.plot(xs, ys, [0] * len(xs), color=PURPLE, lw=0.45)

    texts = []                                                # dimension texts, placed after the view is set so they follow the arrow

    def dimension(p, q, text, shift=(0, 0, 0), **kw):             # a dimension line with arrowheads and its text, outside the sheet
        dx, dy, dz = (q[0] - p[0], q[1] - p[1], q[2] - p[2])
        n = (dx * dx + dy * dy + dz * dz) ** 0.5 or 1.0
        mid = ((p[0] + q[0]) / 2, (p[1] + q[1]) / 2, (p[2] + q[2]) / 2)
        head = min(0.5, 0.02 * big / n * 1.0)
        texts.append(((mid[0] + shift[0], mid[1] + shift[1], mid[2] + shift[2]), p, q, text, kw))

    ext_c = "#94a3b8"
    ax.plot([0, 0], [0, -off * 1.2], [0, 0], color=ext_c, lw=0.6)                       # extension lines of the length
    ax.plot([width, width], [0, -off * 1.2], [0, 0], color=ext_c, lw=0.6)
    dimension((0, -off, 0), (width, -off, 0), f"{width:g}", shift=(0, -off * 0.12, 0), ha="center", va="top")
    ax.plot([width, width + off * 1.2], [0, 0], [0, 0], color=ext_c, lw=0.6)           # of the width
    ax.plot([width, width + off * 1.2], [height, height], [0, 0], color=ext_c, lw=0.6)
    dimension((width + off, 0, 0), (width + off, height, 0), f"{height:g}", shift=(off * 0.12, 0, 0), ha="center", va="top")
    ax.plot([0, -off * 1.2], [height, height], [z, z], color=ext_c, lw=0.6)           # of the thickness (at the back left corner)
    ax.plot([0, -off * 1.2], [height, height], [0, 0], color=ext_c, lw=0.6)
    dimension((-off, height, 0), (-off, height, max(z, 1e-6)), "", ha="right")
    ax.text(-off * 1.15, height, z / 2, f"{thickness:g}", fontsize=FONT_PT, color="#0f172a", ha="right", va="center")

    # the coordinate system at the origin: small, thin, no boxes; the letters sit outside the sheet
    for vec, col, lab, lpos in (((arm, 0, 0), "#dc2626", "X", (arm * 1.35, -arm * 0.7, 0)),
                                ((0, arm, 0), "#16a34a", "Y", (-arm * 0.7, arm * 1.35, 0)),
                                ((0, 0, arm), "#2563eb", "Z", (-arm * 0.6, -arm * 0.6, arm * 0.9))):
        ax.quiver(0, 0, 0, *vec, color=col, linewidth=0.8, arrow_length_ratio=0.22, normalize=False)
        if lab != "Z":
            ax.text(*lpos, lab, color=col, fontsize=6, fontweight="normal", ha="center", va="center")

    zr = max(z, arm * 1.6)
    xr0, xr1 = -0.30 * big * room, width + 0.12 * big * room
    yr0, yr1 = -off * 1.6 * room, height + 0.04 * big * room
    ax.set_xlim(xr0, xr1)
    ax.set_ylim(yr0, yr1)
    ax.set_zlim(0, zr)
    ax.set_box_aspect((xr1 - xr0, yr1 - yr0, zr), zoom=ZOOM)          # equal scale on all three axes: nothing is stretched
    ax.set_proj_type("ortho")                                  # a true isometric view: no perspective
    ax.view_init(elev=30, azim=-135)
    ax.set_axis_off()
    import math
    from mpl_toolkits.mplot3d import proj3d
    fig.canvas.draw()
    zx, zy = ax.transData.transform(proj3d.proj_transform(0, 0, arm, ax.get_proj())[:2])     # the Z letter, on top of everything
    fig.text(zx / fig.bbox.width, (zy + 0.018 * fig.bbox.height) / fig.bbox.height, "Z", color="#2563eb", fontsize=6,
             ha="center", va="center")
    cx, cy = ax.transData.transform(proj3d.proj_transform(width / 2, height / 2, 0, ax.get_proj())[:2])
    for pos, p, q, text, kw in texts:                          # the text runs along its arrow, on the outer side of it
        (x1, y1), (x2, y2) = [ax.transData.transform(proj3d.proj_transform(*c, ax.get_proj())[:2]) for c in (p, q)]
        ang = math.degrees(math.atan2(y2 - y1, x2 - x1))
        ang = ang + 180 if ang > 90 else ang - 180 if ang < -90 else ang
        mx, my = (x1 + x2) / 2, (y1 + y2) / 2
        tpx = len(text) * 0.62 * FONT_PT * fig.dpi / 72 * 1.3      # room for the number in the middle of the arrow
        f = min(0.9, tpx / (math.hypot(x2 - x1, y2 - y1) or 1.0)) if text else 0.0
        dx, dy, dz = (q[0] - p[0], q[1] - p[1], q[2] - p[2])
        mid = tuple((a_ + b_) / 2 for a_, b_ in zip(p, q))
        for sign in (1, -1):                                       # two arrows from the gap to the ends, heads at the ends
            start = tuple(m + sign * f / 2 * d for m, d in zip(mid, (dx, dy, dz)))
            vec = tuple(sign * (1 - f) / 2 * d for d in (dx, dy, dz))
            ax.quiver(*start, *vec, color="#334155", linewidth=0.5, arrow_length_ratio=min(0.5, 0.02 * big / ((dx * dx + dy * dy + dz * dz) ** 0.5 * (1 - f) / 2 or 1)),
                      normalize=False)
        if text:
            fig.text(mx / fig.bbox.width, my / fig.bbox.height, text, fontsize=FONT_PT, color="#0f172a", rotation=ang,
                     rotation_mode="anchor", ha="center", va="center")
    return _png(fig)


@st.cache_data(max_entries=12, show_spinner=False)
def plan_png(width: float, height: float, chain_pts: tuple, kinds: tuple, labels: tuple = (), version: int = PICTURE_VERSION) -> bytes:
    fig, ax = plt.subplots(figsize=(8, 8 * height / width if width else 5), dpi=200)
    ax.add_patch(plt.Rectangle((0, 0), width, height, fill=False, lw=1.2, color="black"))
    for pts, kind in zip(chain_pts, kinds):
        xs, ys = zip(*pts)
        ax.plot(xs, ys, color=PURPLE, lw=0.7, ls="-" if kind == "Outline" else "--")
    for x, y, text, size in labels:                       # the cutting order and the part names
        ax.text(x, y, text, fontsize=size, ha="center", va="center", color="#1e293b", clip_on=True)
    ax.plot([0], [0], "o", color="black", ms=4)
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
    if st.session_state.get("camming_loaded_pid") != pid:                # another operation: its saved values
        st.session_state.camming_thickness = float(_saved(db, pid).get("thickness", 18.0))
        n_sheets = max(1, len(result or []))
        st.session_state.camming_sheet = min(max(1, int(_saved(db, pid).get("sheet", 1))), n_sheets)
        st.session_state.camming_loaded_pid = pid
    left, _ = st.columns([1, 2])                              # the thickness and the sheet above each other, wide enough for their text
    left.number_input("Material thickness", min_value=0.1, step=0.5, key="camming_thickness",
                      help="Height of the stock: the top of the material is at Z = thickness.")
    sheet_slot = left.container()                             # the sheet selector is drawn here (it needs the run), under the thickness

    def _save_all(sheet_no=None, ch=None):
        """The one Save of this page: the thickness, the sheet and the cutting order (the working copy of the order editor)."""
        ss_ = st.session_state
        try:
            cam_ = db.get_cam(pid) or {}
            c_ = {**cam_.get("camming", {}), "thickness": float(ss_.camming_thickness)}
            if sheet_no is not None:
                c_["sheet"] = int(sheet_no)
                base, work = ss_.get("geo_work_base"), ss_.get("geo_work")
                if work is not None and base and base[0] == pid and base[1] == sheet_no:
                    if list(work) == default_order(ch):           # the default order is not stored
                        c_.pop("order", None)
                        c_.pop("order_sheet", None)
                    else:
                        c_["order"], c_["order_sheet"] = list(work), int(sheet_no)
            cam_["camming"] = c_
            db.save_cam(pid, cam_)
            ss_.camming_saved_msg = "Saved with the operation."
        except Exception as e:  # noqa: BLE001
            ss_.camming_save_error = f"Could not save: {e}"

    msg = st.session_state.pop("camming_saved_msg", None)
    if msg:
        st.toast(msg, icon=":material/check_circle:")
    if st.session_state.get("camming_save_error"):
        st.error(st.session_state.pop("camming_save_error"))
    if not result:
        st.info("Nest first: the geometry is taken from the sheets of the current run.")
        st.button("Save", key="camming_save", type="primary", on_click=_save_all)
        return
    if not 1 <= st.session_state.get("camming_sheet", 1) <= len(result):
        st.session_state.camming_sheet = 1
    sheet_no = sheet_slot.selectbox("Sheet of this operation", list(range(1, len(result) + 1)), key="camming_sheet",
                                    format_func=lambda i: f"Sheet {i}")
    sheet = result[sheet_no - 1]
    ch = chains(sheet)
    held = [p["part"] for r in result for p in r["placements"] if is_held(p)]
    if held:
        st.warning(f"{len(held)} part(s) are in the hold area and are not part of the geometry: {', '.join(held)}")
    n_out = sum(1 for c in ch if c["type"] == "Outline")
    st.metric("Outlines", n_out)
    ss = st.session_state
    saved_ids = full_order(ch, saved_order(db, pid, sheet_no))        # the order that is saved (the plan view shows it)
    by_id = {c["id"]: c for c in ch}
    rank = {cid: i for i, cid in enumerate(saved_ids, 1)}

    k = 1.0                                                   # always drawn to true scale
    st.image(isometric_png(float(sheet["width"]), float(sheet["height"]), float(st.session_state.camming_thickness), solids(sheet), k, PICTURE_VERSION),
             caption="The whole sheet seen from the origin corner (lower left corner in plan view), drawn to scale: X to the right, "
                     "Y to the back, Z up. Every part is drawn transparent with dotted edges and its height; its bottom contour (the geometry) is red.")
    pts = tuple(tuple(c["pts"]) for c in ch)
    labels = tuple((c["label_pt"][0], c["label_pt"][1], f"{rank[c['id']]}  {c['part']}" if c["type"] == "Outline" else str(rank[c["id"]]),
                    5 if c["type"] == "Outline" else 4) for c in ch)
    st.image(plan_png(float(sheet["width"]), float(sheet["height"]), pts, tuple(c["type"] for c in ch), labels, PICTURE_VERSION),
             caption="Plan view: outlines solid, holes dashed; every contour has its saved cutting number (and the part name). "
                     "The black dot is the origin. Change the order in the table below; Save at the bottom of the page stores it.")
    order_editor(db, pid, sheet_no, ch, saved_ids)
    st.button("Save", key="camming_save", type="primary", on_click=_save_all, args=(sheet_no, ch),
              help="Saves everything on this page: the thickness, the sheet and the cutting order.")      # the very bottom of the page


@st.fragment
def order_editor(db, pid, sheet_no: int, ch: list, saved_ids: list):
    """The cutting order of the contours. A click here redraws only this part of the page (a fragment): the order is changed on a
    working copy at once, and Save order stores it (then the whole page, with the plan view, is redrawn)."""
    ss = st.session_state
    by_id = {c["id"]: c for c in ch}
    base = (pid, sheet_no, tuple(saved_ids))
    if ss.get("geo_work_base") != base:                  # another operation / sheet, or the order was saved: start from the saved order
        ss.geo_work_base, ss.geo_work = base, list(saved_ids)
        ss.geo_move_ver = ss.get("geo_move_ver", 0) + 1
    work = ss.geo_work
    rank = {cid: i for i, cid in enumerate(work, 1)}
    ver = ss.setdefault("geo_move_ver", 0)               # a new table key after every change: the table is redrawn with the row highlighted
    tkey = f"geo_tbl_{ver}"
    state = ss.get(tkey)                                 # a click on a row of the table selects that contour
    rows = tuple(state["selection"]["rows"]) if state else ()
    if rows != ss.get("geo_tbl_prev", ()):
        ss.geo_tbl_prev = rows
        if rows and rows[0] < len(work):
            ss.geo_move_sel = work[rows[0]]
    if ss.get("geo_move_sel") not in work:
        ss.geo_move_sel = None
    sel = ss.geo_move_sel

    def _move(kind):
        cid = ss.get("geo_move_sel")
        if cid not in work:
            return
        new = list(work)
        i = new.index(cid)
        new.pop(i)
        j = {"up": max(0, i - 1), "down": min(len(new), i + 1), "first": 0, "last": len(new)}[kind]
        new.insert(j, cid)
        ss.geo_work = new
        ss.geo_move_ver += 1
        ss.geo_tbl_prev = ()

    def _reset(to):
        ss.geo_work = list(to)
        ss.geo_move_ver += 1
        ss.geo_tbl_prev = ()

    st.markdown("**Cutting order of the contours**")
    if sel is None:
        st.caption("Click a row in the table to select a contour, then move it with the buttons.")
    else:
        st.caption(f"Selected: **{rank[sel]} · {by_id[sel]['part']} ({by_id[sel]['type'].lower()})**, highlighted in the table.")
    with st.container(key="order_btns"):                   # (the phone keeps the five buttons side by side)
        b1, b2, b3, b4, b5 = st.columns(5)
    for col, kind, label in ((b1, "up", "▲ Up"), (b2, "down", "▼ Down"), (b3, "first", "⤒ First"), (b4, "last", "⤓ Last")):
        col.button(label, on_click=_move, args=(kind,), key=f"geo_move_{kind}", width="stretch", disabled=sel is None)
    b5.button("Default order", on_click=_reset, args=(default_order(ch),), key="geo_order_reset", width="stretch",
              help="The holes first, then the outlines, each from the lower left corner of the part, left to right.")
    df = pd.DataFrame([{"No.": rank[cid], "Part": by_id[cid]["part"], "Type": by_id[cid]["type"],
                        "Length": round(by_id[cid]["length"], 1), "Z": 0.0} for cid in work])
    hi = work.index(sel) if sel in work else -1
    styled = df.style.apply(lambda r: ["background-color: #cfe9f7; color: #0f3d57; font-weight: 600"] * len(r) if r.name == hi else [""] * len(r), axis=1).format({"Length": "{:.1f}", "Z": "{:.0f}"})
    st.dataframe(styled, hide_index=True, width="content", on_select="rerun", selection_mode="single-row", key=tkey)
    changed = list(work) != list(saved_ids)
    if changed:
        st.warning("The order has changed and is not saved yet: press Save at the bottom of the page. The plan view and the G-code "
                   "still use the saved order until then.")
    d1, _ = st.columns([1, 3])
    d1.button("Discard changes", on_click=_reset, args=(saved_ids,), key="geo_order_discard", disabled=not changed, width="stretch")


def camming_tab(db, pid, result, project: str = "", meta: dict | None = None, run_id=None, file_base: str = "nest"):
    """The NestoCAM tab: the operations of the current run; an open operation shows its five steps."""
    from operations_ui import operations_tab
    operations_tab(db, pid, result, project, meta, run_id)
