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


def draw_sheet(sheet, title=""):
    fig, ax = plt.subplots(figsize=(8, 8 * sheet.height / sheet.width if sheet.width else 5))
    ax.add_patch(Rectangle((0, 0), sheet.width, sheet.height, fill=False, lw=2, color="black"))
    cmap = plt.get_cmap("tab20")
    names = {}
    for p in sheet.placements:
        c = cmap(names.setdefault(p.part, len(names)) % 20)
        if p.polys:
            for poly in p.polys:
                ax.add_patch(_poly_patch(poly, facecolor=c, alpha=0.5, edgecolor="black", lw=0.8))
        else:
            ax.add_patch(Rectangle((p.x, p.y), p.w, p.h, facecolor=c, alpha=0.5, edgecolor="black", lw=0.8))
        ax.text(p.x + p.w / 2, p.y + p.h / 2, p.part, ha="center", va="center", fontsize=7)
    ax.set_xlim(-0.02 * sheet.width, sheet.width * 1.02)
    ax.set_ylim(-0.02 * sheet.height, sheet.height * 1.02)
    ax.set_aspect("equal")
    ax.set_title(f"{title}  –  {sheet.utilization:.1%} used")
    return fig
