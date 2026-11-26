"""Draw nested sheets with matplotlib."""
from __future__ import annotations

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import PathPatch, Rectangle
from matplotlib.path import Path


def _poly_patch(poly, **kw):
    verts, codes = [], []
    for ring in [poly["ext"], *poly.get("holes", [])]:
        verts += [tuple(p) for p in ring]
        codes += [Path.MOVETO] + [Path.LINETO] * (len(ring) - 2) + [Path.CLOSEPOLY]
    return PathPatch(Path(verts, codes), **kw)


REF_FONT = 7.0      # pt: the size a name gets when the part is big enough
MIN_FONT = 1.5      # pt: the smallest size a name is shrunk to


def _part_geom(p):
    from shapely.geometry import box
    from shapes import geom_from_json
    return geom_from_json(p.polys) if p.polys else box(p.x, p.y, p.x + p.w, p.y + p.h)


def _label_spot(geom, w7, h7):
    """(centre x, centre y, font size, rotation) of the biggest text that fits entirely inside the part. w7 / h7 are the
    width / height (in drawing units) of the name at REF_FONT; the text scales linearly with the font size."""
    from shapely.geometry import Point, box
    from shapely.ops import polylabel

    # the middle of the bounding box and the centre of mass first (a big rectangle gets its name in the middle), then the
    # point farthest from the outline, then any point inside
    cands = [geom.centroid, box(*geom.bounds).centroid, polylabel(geom, tolerance=max(geom.length, 1e-6) / 200.0),
             geom.representative_point()]
    best = (cands[2].x, cands[2].y, MIN_FONT, 0)
    for rot, (tw, th) in ((0, (w7, h7)), (90, (h7, w7))):
        for c in cands:
            if not geom.contains(Point(c.x, c.y)):
                continue

            def ok(f, c=c, tw=tw, th=th):
                k = f / REF_FONT
                return geom.contains(box(c.x - tw * k / 2, c.y - th * k / 2, c.x + tw * k / 2, c.y + th * k / 2))
            if not ok(MIN_FONT):
                continue
            lo, hi = MIN_FONT, REF_FONT
            if ok(hi):
                lo = hi
            else:
                for _ in range(14):
                    mid = (lo + hi) / 2
                    lo, hi = (mid, hi) if ok(mid) else (lo, mid)
            if lo > best[2] + 1e-9:
                best = (c.x, c.y, lo, rot)
    return best


OUT_BELOW = 5.0     # pt: a name that would have to be smaller than this goes outside the sheet instead


def _spread(ideal, sizes, lo, hi):
    """Positions along a side for labels that want to sit at `ideal` (sorted) and need `sizes` of room each: they keep
    close to their ideal spot, never overlap, and stay within [lo, hi] when there is room."""
    n = len(ideal)
    pos = list(ideal)
    for i in range(1, n):
        pos[i] = max(pos[i], pos[i - 1] + (sizes[i - 1] + sizes[i]) / 2)
    if n and pos[-1] > hi:
        pos[-1] = hi
        for i in range(n - 2, -1, -1):
            pos[i] = min(pos[i], pos[i + 1] - (sizes[i] + sizes[i + 1]) / 2)
    if n and pos[0] < lo:                                  # more labels than room: pack them from the start
        pos[0] = lo
        for i in range(1, n):
            pos[i] = max(pos[i], pos[i - 1] + (sizes[i - 1] + sizes[i]) / 2)
    return pos


def _side_of(ax_, ay, W, H):
    """The sheet edge nearest to a point: where the label of that part goes ("l", "r", "b", "t")."""
    d = {"l": ax_, "r": W - ax_, "b": ay, "t": H - ay}
    return min(d, key=d.get)


def part_color_indices(sheets_placements) -> dict:
    """One colour index per part NAME for a whole run: in the order the names first appear over all the sheets, so a
    part has the same colour on every sheet. `sheets_placements`: an iterable of placement lists (objects or dicts)."""
    idx = {}
    for placements in sheets_placements:
        for p in placements:
            idx.setdefault(p["part"] if isinstance(p, dict) else p.part, len(idx))
    return idx


def place_names(fig, ax, W, H, labels):
    """Write the names of the parts on a drawing of a W x H sheet. labels: [(name, shapely geometry of the part)].
    Part names: inside the part, as large as fits, but not smaller than OUT_BELOW pt. A part too small for that gets
    its name OUTSIDE the sheet, on the sheet edge nearest to it (so the names are spread around the sheet, not
    collected on one side), with a thin leader line and a dot on the part. Sets the limits of the axes (room for the names)."""
    renderer = fig.canvas.get_renderer()
    room = {"l": 0.0, "r": 0.0, "b": 0.0, "t": 0.0}          # extra room beside the sheet for the outside names
    plan = None
    for _ in range(4):                                       # the room shrinks the drawing a little: settle on it
        ax.set_xlim(-0.02 * W - room["l"], W * 1.02 + room["r"])
        ax.set_ylim(-0.02 * H - room["b"], H * 1.02 + room["t"])
        fig.canvas.draw()
        px_x = abs(ax.transData.transform((1, 0))[0] - ax.transData.transform((0, 0))[0])      # pixels per drawing unit
        px_y = abs(ax.transData.transform((0, 1))[1] - ax.transData.transform((0, 0))[1])
        probe = {}
        for name, _g in labels:
            if name not in probe:
                t = ax.text(0, 0, name, fontsize=REF_FONT)
                bb = t.get_window_extent(renderer)
                t.remove()
                probe[name] = (bb.width / px_x, bb.height / px_y)
        inside, outside = [], []
        for name, g in labels:
            w7, h7 = probe[name]
            cx, cy, fs, rot = _label_spot(g, w7, h7)
            if fs >= OUT_BELOW:
                inside.append((name, cx, cy, fs, rot))
            else:
                pt = g.representative_point() if not g.contains(g.centroid) else g.centroid
                outside.append((name, pt.x, pt.y))
        # each outside name goes to the sheet edge nearest to its part; along an edge they keep their order
        sides = {k: [] for k in "lrbt"}
        for name, ax_, ay in outside:
            sides[_side_of(ax_, ay, W, H)].append((name, ax_, ay))
        pad = 0.012 * max(W, H)
        need = {"l": 0.0, "r": 0.0, "b": 0.0, "t": 0.0}
        plan = []
        for side, items in sides.items():
            if not items:
                continue
            horizontal = side in "bt"                                        # names along the bottom / top edge
            items.sort(key=lambda a: a[1] if horizontal else a[2])
            size = [probe[n][0] if horizontal else probe[n][1] * 1.35 for n, _, _ in items]
            length = W if horizontal else H
            fs = REF_FONT
            total = sum(size) + pad * (len(items) - 1)
            if total > length * 1.0:                                         # too many for this edge: a smaller font
                fs = max(4.0, REF_FONT * length / total)
                size = [v * fs / REF_FONT for v in size]
            ideal = [a[1] if horizontal else a[2] for a in items]
            pos = _spread(ideal, [v + pad * 0.5 for v in size], 0.0, length)
            for (n, ax_, ay), q in zip(items, pos):
                plan.append((side, n, ax_, ay, q, fs))
            need[side] = (probe[items[0][0]][1] * 1.4 * fs / REF_FONT + 2 * pad) if horizontal else \
                (max(probe[n][0] for n, _, _ in items) * fs / REF_FONT + 2.5 * pad)
        if all(abs(need[k] - room[k]) < 0.02 * max(W, H) for k in need):
            room = need
            break
        room = need
    for name, cx, cy, fs, rot in inside:
        ax.text(cx, cy, name, ha="center", va="center", fontsize=fs, rotation=rot, rotation_mode="anchor")
    for side, name, ax_, ay, q, fs in plan:
        x, y, ha, va = {"l": (-pad, q, "right", "center"), "r": (W + pad, q, "left", "center"),
                        "b": (q, -pad, "center", "top"), "t": (q, H + pad, "center", "bottom")}[side]
        ax.annotate(name, xy=(ax_, ay), xytext=(x, y), textcoords="data", ha=ha, va=va, fontsize=fs,
                    arrowprops=dict(arrowstyle="-", lw=0.5, color="0.35", shrinkA=1, shrinkB=0), annotation_clip=False)
        ax.plot([ax_], [ay], marker="o", ms=2, color="0.2", zorder=5)


def draw_sheet(sheet, title="", scrap=None, color_index=None):
    """scrap: optional list of free rectangles (x, y, w, h) to outline as usable offcuts.
    Part names: inside the part, as large as fits, but not smaller than OUT_BELOW pt. A part too small for that gets
    its name OUTSIDE the sheet, on the sheet edge nearest to it (so the names are spread around the sheet, not
    collected on one side), with a thin leader line and a dot on the part."""
    W, H = sheet.width, sheet.height
    fig, ax = plt.subplots(figsize=(8, 8 * H / W if W else 5), dpi=170)
    ax.add_patch(Rectangle((0, 0), W, H, fill=False, lw=2, color="black"))
    cmap = plt.get_cmap("tab20")
    names = {}
    for p in sheet.placements:
        c = cmap((color_index[p.part] if color_index and p.part in color_index else names.setdefault(p.part, len(names))) % 20)
        if p.polys:
            for poly in p.polys:
                ax.add_patch(_poly_patch(poly, facecolor=c, alpha=0.5, edgecolor="black", lw=0.8))
        else:
            ax.add_patch(Rectangle((p.x, p.y), p.w, p.h, facecolor=c, alpha=0.5, edgecolor="black", lw=0.8))
    for i, (x, y, w, h) in enumerate(scrap or []):
        ax.add_patch(Rectangle((x, y), w, h, fill=False, ls="--", lw=1.3, edgecolor="#2a9d4a"))
        if w > 0.12 * W and h > 0.05 * H:
            ax.text(x + w / 2, y + h / 2, f"scrap {w:.0f} × {h:.0f}", ha="center", va="center",
                    fontsize=8, color="#2a9d4a")
    ax.set_aspect("equal")
    ax.set_title(f"{title}  –  {sheet.utilization:.2%} used  ·  cut length {sheet.cut_length:,.0f}"
                 + (f"  ·  largest offcut {scrap[0][2]:.0f} × {scrap[0][3]:.0f}" if scrap else ""))

    place_names(fig, ax, W, H, [(p.part, g) for p, g in zip(sheet.placements, [_part_geom(p) for p in sheet.placements])])
    return fig
