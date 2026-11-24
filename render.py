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


def draw_sheet(sheet, title="", scrap=None):
    """scrap: optional list of free rectangles (x, y, w, h) to outline as usable offcuts.
    Every part carries its name inside it: at the normal size when the part is big enough, otherwise as large as
    fits (rotated by 90 degrees when that gives a larger size)."""
    W, H = sheet.width, sheet.height
    fig, ax = plt.subplots(figsize=(8, 8 * H / W if W else 5), dpi=170)
    ax.add_patch(Rectangle((0, 0), W, H, fill=False, lw=2, color="black"))
    cmap = plt.get_cmap("tab20")
    names = {}
    for p in sheet.placements:
        c = cmap(names.setdefault(p.part, len(names)) % 20)
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
    ax.set_xlim(-0.02 * W, W * 1.02)
    ax.set_ylim(-0.02 * H, H * 1.02)
    ax.set_aspect("equal")
    ax.set_title(f"{title}  –  {sheet.utilization:.2%} used  ·  cut length {sheet.cut_length:,.0f}"
                 + (f"  ·  largest offcut {scrap[0][2]:.0f} × {scrap[0][3]:.0f}" if scrap else ""))

    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    px_x = abs(ax.transData.transform((1, 0))[0] - ax.transData.transform((0, 0))[0])      # pixels per drawing unit
    px_y = abs(ax.transData.transform((0, 1))[1] - ax.transData.transform((0, 0))[1])
    probe_cache = {}
    for p in sheet.placements:
        if p.part not in probe_cache:
            probe = ax.text(0, 0, p.part, fontsize=REF_FONT)
            bb = probe.get_window_extent(renderer)
            probe.remove()
            probe_cache[p.part] = (bb.width / px_x, bb.height / px_y)
        w7, h7 = probe_cache[p.part]
        cx, cy, fs, rot = _label_spot(_part_geom(p), w7, h7)
        ax.text(cx, cy, p.part, ha="center", va="center", fontsize=fs, rotation=rot, rotation_mode="anchor")
    return fig
