"""Draw nested sheets with matplotlib."""
from __future__ import annotations

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle


def draw_sheet(sheet, title=""):
    fig, ax = plt.subplots(figsize=(8, 8 * sheet.height / sheet.width if sheet.width else 5))
    ax.add_patch(Rectangle((0, 0), sheet.width, sheet.height, fill=False, lw=2, color="black"))
    cmap = plt.get_cmap("tab20")
    names = {}
    for p in sheet.placements:
        c = cmap(names.setdefault(p.part, len(names)) % 20)
        ax.add_patch(Rectangle((p.x, p.y), p.w, p.h, facecolor=c, alpha=0.35, edgecolor="black", lw=0.8))
        if p.outline:
            for pl in p.outline:
                ax.plot([p.x + x for x, _ in pl], [p.y + y for _, y in pl], color="black", lw=0.8)
        ax.text(p.x + p.w / 2, p.y + p.h / 2, p.part + (" ↻" if p.rotated else ""),
                ha="center", va="center", fontsize=7)
    ax.set_xlim(-0.02 * sheet.width, sheet.width * 1.02)
    ax.set_ylim(-0.02 * sheet.height, sheet.height * 1.02)
    ax.set_aspect("equal")
    ax.set_title(f"{title}  –  {sheet.utilization:.1%} used")
    return fig
