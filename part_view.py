"""Dimensioned drawing of a single part (outline, holes, overall size, edge lengths, diameters)."""
from __future__ import annotations

import io
import math
from collections import Counter

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from shapely.geometry import Polygon
from shapely.geometry.polygon import orient

from render import _poly_patch
from shapes import geom_to_json


def fmt(v: float) -> str:
    """12 / 12.5 / 0.75 - snaps to a whole number when the part is a polygon approximation of a round value."""
    r = round(v)
    if abs(v - r) <= max(0.02, 4e-4 * abs(v)):
        return f"{r:d}"
    return f"{v:.2f}".rstrip("0").rstrip(".") if abs(v) < 10 else f"{v:.1f}"


def _polygons(geom):
    return [geom] if geom.geom_type == "Polygon" else [g for g in geom.geoms if g.geom_type == "Polygon"]


def circle_fit(ring):
    """(cx, cy, diameter) if the closed ring is a circle (polygon approximation), else None."""
    pts = list(ring.coords)[:-1]
    if len(pts) < 12:
        return None
    poly = Polygon(pts)
    if poly.area <= 0:
        return None
    c = poly.centroid
    r = [math.hypot(x - c.x, y - c.y) for x, y in pts]
    mean = sum(r) / len(r)
    if mean <= 0 or (max(r) - min(r)) / mean > 0.03:
        return None
    # mean of vertex radius and edge-midpoint radius: exact for both inscribed and circumscribed polygons
    mids = [math.hypot((pts[i][0] + pts[(i + 1) % len(pts)][0]) / 2 - c.x,
                       (pts[i][1] + pts[(i + 1) % len(pts)][1]) / 2 - c.y) for i in range(len(pts))]
    return c.x, c.y, mean + sum(mids) / len(mids)


def part_stats(geom):
    """Facts shown next to the drawing."""
    minx, miny, maxx, maxy = geom.bounds
    holes = []
    for poly in _polygons(geom):
        for ring in poly.interiors:
            cf = circle_fit(ring)
            if cf:
                holes.append(("circle", cf[2]))
            else:
                x0, y0, x1, y1 = Polygon(ring).bounds
                holes.append(("other", (x1 - x0, y1 - y0)))
    outer = [circle_fit(p.exterior) for p in _polygons(geom)]
    return {
        "width": maxx - minx, "height": maxy - miny, "area": geom.area, "cut_length": geom.length,
        "holes": holes, "round": len(outer) == 1 and outer[0] is not None,
        "diameter": outer[0][2] if len(outer) == 1 and outer[0] else None,
        "pieces": len(_polygons(geom)),
    }


def hole_summary(holes):
    """['4× Ø12', '2× 30 × 10'] - holes grouped by size."""
    c = Counter()
    for kind, v in holes:
        c[("Ø" + fmt(v)) if kind == "circle" else f"{fmt(v[0])} × {fmt(v[1])}"] += 1
    return [f"{n}× {label}" if n > 1 else label for label, n in c.items()]


def _label_edges(ax, poly, m, W, H):
    """Length of every straight edge of a simple polygon (skips edges equal to the overall width/height)."""
    simple = orient(poly.simplify(0.003 * m, preserve_topology=True), 1.0)
    pts = list(simple.exterior.coords)[:-1]
    if len(pts) > 8 or simple.interiors and len(list(simple.interiors)) > 0 and False:
        return
    for i in range(len(pts)):
        (x0, y0), (x1, y1) = pts[i], pts[(i + 1) % len(pts)]
        dx, dy = x1 - x0, y1 - y0
        length = math.hypot(dx, dy)
        if length < 0.06 * m:
            continue
        axis_aligned = abs(dy) < 1e-6 * m or abs(dx) < 1e-6 * m
        if axis_aligned and (abs(length - W) < 0.005 * m or abs(length - H) < 0.005 * m):
            continue  # already shown as the overall dimension
        nx, ny = dy / length, -dx / length  # outward normal of a counter-clockwise ring
        ang = math.degrees(math.atan2(dy, dx))
        if ang > 90:
            ang -= 180
        elif ang < -90:
            ang += 180
        ax.text((x0 + x1) / 2 + nx * 0.045 * m, (y0 + y1) / 2 + ny * 0.045 * m, fmt(length), rotation=ang,
                ha="center", va="center", fontsize=9, color="#1d4ed8")


def draw_part(geom, title="", qty_text=""):
    """Matplotlib figure of the part with its main dimensions."""
    minx, miny, maxx, maxy = geom.bounds
    W, H = maxx - minx, maxy - miny
    m = max(W, H)
    fig = plt.figure(figsize=(7.5, 5.6))                       # one fixed canvas for every part
    ax = fig.add_axes([0.02, 0.07, 0.96, 0.86])                 # equal aspect then scales the part to fit it
    for poly in geom_to_json(geom):
        ax.add_patch(_poly_patch(poly, facecolor="#cfe0f1", edgecolor="#1f2937", lw=1.6))

    stats = part_stats(geom)
    blue = "#1d4ed8"
    off = 0.12 * m
    # overall width (below) and height (right)
    y = miny - off
    for x in (minx, maxx):
        ax.plot([x, x], [miny - 0.02 * m, y - 0.02 * m], color="#6b7280", lw=0.8)
    ax.annotate("", xy=(maxx, y), xytext=(minx, y), arrowprops=dict(arrowstyle="<->", color=blue, lw=1.2))
    wlabel = f"Ø {fmt(stats['diameter'])}" if stats["round"] else fmt(W)
    ax.text((minx + maxx) / 2, y - 0.025 * m, wlabel, ha="center", va="top", fontsize=11, color=blue, weight="bold")
    if not stats["round"]:
        x = maxx + off
        for yy in (miny, maxy):
            ax.plot([maxx + 0.02 * m, x + 0.02 * m], [yy, yy], color="#6b7280", lw=0.8)
        ax.annotate("", xy=(x, maxy), xytext=(x, miny), arrowprops=dict(arrowstyle="<->", color=blue, lw=1.2))
        ax.text(x + 0.03 * m, (miny + maxy) / 2, fmt(H), rotation=90, ha="left", va="center", fontsize=11,
                color=blue, weight="bold")

    polys = _polygons(geom)
    if len(polys) == 1 and not stats["round"]:
        _label_edges(ax, polys[0], m, W, H)
    # holes: label each when there are few, otherwise only the summary
    n_holes = len(stats["holes"])
    for poly in polys:
        for ring in poly.interiors:
            cf = circle_fit(ring)
            x0, y0, x1, y1 = Polygon(ring).bounds
            if n_holes <= 12:
                label = f"Ø{fmt(cf[2])}" if cf else f"{fmt(x1 - x0)}×{fmt(y1 - y0)}"
                cx, cy = (cf[0], cf[1]) if cf else ((x0 + x1) / 2, (y0 + y1) / 2)
                ax.text(cx, cy, label, ha="center", va="center", fontsize=8, color="#b45309")
    summary = hole_summary(stats["holes"])
    if summary:
        fig.text(0.02, 0.02, "Holes: " + ",  ".join(summary), fontsize=10, color="#b45309")
    ax.set_xlim(minx - 0.12 * m, maxx + 0.30 * m)
    ax.set_ylim(miny - 0.30 * m, maxy + 0.12 * m)
    ax.set_aspect("equal", adjustable="box", anchor="C")
    ax.axis("off")
    if title:
        ax.set_title(title + (f"   {qty_text}" if qty_text else ""), fontsize=13, weight="bold")
    return fig


PNG_SIZE = (825, 616)  # pixels of every part drawing (7.5 x 5.6 in at 110 dpi)


def part_png(geom, title="", dpi=110) -> bytes:
    """The drawing as a PNG of ALWAYS the same size - small and huge parts are scaled to the same box
    (no tight cropping, which would stretch tall or wide parts to the full page width)."""
    fig = draw_part(geom, title)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=dpi, facecolor="white")
    plt.close(fig)
    return buf.getvalue()
