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


def _anchor(p):
    """A point inside the part (for the leader line of an outside label)."""
    if p.polys:
        from shapes import geom_from_json
        pt = geom_from_json(p.polys).representative_point()
        return pt.x, pt.y
    return p.x + p.w / 2, p.y + p.h / 2


def _fits(ax, renderer, t, p, share=0.92):
    """Does the text t fit inside the bounding box of the part?"""
    bb = t.get_window_extent(renderer)
    (x0, y0), (x1, y1) = ax.transData.transform((p.x, p.y)), ax.transData.transform((p.x + p.w, p.y + p.h))
    return bb.width <= share * abs(x1 - x0) and bb.height <= share * abs(y1 - y0)


def draw_sheet(sheet, title="", scrap=None):
    """scrap: optional list of free rectangles (x, y, w, h) to outline as usable offcuts.
    A part name that does not fit inside its (small) part is written outside the sheet, in a column to the right, with a
    leader line to the part."""
    W, H = sheet.width, sheet.height
    fig, ax = plt.subplots(figsize=(8, 8 * H / W if W else 5))
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
    ax.set_ylim(-0.02 * H, H * 1.02)
    ax.set_aspect("equal")
    ax.set_title(f"{title}  –  {sheet.utilization:.2%} used  ·  cut length {sheet.cut_length:,.0f}"
                 + (f"  ·  largest offcut {scrap[0][2]:.0f} × {scrap[0][3]:.0f}" if scrap else ""))

    # part names: inside the part when they fit, otherwise outside the sheet with a leader line
    renderer = fig.canvas.get_renderer()
    outside = []
    margin = 0.0
    for _ in range(3):                                   # the extra room on the right shrinks the drawing a little: re-check
        ax.set_xlim(-0.02 * W, W * (1.02 + margin))
        fig.canvas.draw()
        outside = []
        for p in sheet.placements:
            t = ax.text(p.x + p.w / 2, p.y + p.h / 2, p.part, ha="center", va="center", fontsize=7)
            if not _fits(ax, renderer, t, p):
                t.remove()
                outside.append(p)
        need = 0.17 if outside else 0.0
        if need == margin:
            break
        margin = need
    if outside:
        _outside_labels(ax, renderer, sheet, outside)
    return fig


def _outside_labels(ax, renderer, sheet, small):
    W, H = sheet.width, sheet.height
    probe = ax.text(0, 0, "Hg", fontsize=7)
    px_per_y = abs(ax.transData.transform((0, 1))[1] - ax.transData.transform((0, 0))[1])
    line7 = probe.get_window_extent(renderer).height / px_per_y * 1.35          # data units one 7 pt label needs
    probe.remove()
    # many labels: a smaller font so that they fit next to the sheet's height (not below 4 pt)
    fs = max(4.0, min(7.0, 7.0 * 1.1 * H / (len(small) * line7))) if len(small) * line7 > 1.1 * H else 7.0
    line_h = line7 * fs / 7.0
    # top to bottom; in a row the rightmost part gets the highest label, so that the leader lines cross as little as possible
    items = sorted(((*_anchor(p), p.part) for p in small), key=lambda a: (-round(a[1] / max(line_h, 1e-9)), -a[0]))
    ys = []
    for _, ay, _ in items:                                                     # near the part, but never on top of each other
        ys.append(min(ay, ys[-1] - line_h) if ys else min(ay, H))
    if ys and ys[-1] < 0 and ys[0] - ys[-1] <= H:                              # keep the column beside the sheet, not below it
        ys = [y - ys[-1] for y in ys]
    x_label = W * 1.03
    for (ax_, ay, name), y in zip(items, ys):
        ax.annotate(name, xy=(ax_, ay), xytext=(x_label, y), textcoords="data", ha="left", va="center", fontsize=fs,
                    arrowprops=dict(arrowstyle="-", lw=0.5, color="0.35", shrinkA=1, shrinkB=0, relpos=(0, 0.5)),
                    annotation_clip=False)
        ax.plot([ax_], [ay], marker="o", ms=2, color="0.2", zorder=5)
