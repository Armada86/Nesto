"""Part shapes: parametric builders and (de)serialisation of shapely geometry to JSON."""
from __future__ import annotations

import math

from shapely import affinity
from shapely.geometry import LineString, MultiPolygon, Point, Polygon, box
from shapely.geometry.polygon import orient
from shapely.ops import unary_union

# shapes that can be typed in the parts table (A = width/diameter, B = height)
SHAPES = ["rectangle", "circle", "ellipse", "triangle", "right triangle", "obround", "dxf"]

_QUAD = 32  # segments per quarter of a circle/ellipse


def normalize(geom):
    """Move the geometry so its bounding-box lower-left corner is at (0, 0)."""
    minx, miny, _, _ = geom.bounds
    return affinity.translate(geom, -minx, -miny)


def _ellipse(a: float, b: float):
    # circumscribed polygon (slightly larger than the true ellipse), so the real
    # cut part never overlaps its neighbours
    n = 4 * _QUAD
    g = Point(0, 0).buffer(1.0, quad_segs=_QUAD)
    g = affinity.scale(g, a / 2 / math.cos(math.pi / n), b / 2 / math.cos(math.pi / n), origin=(0, 0))
    return g


def build_shape(kind: str, a: float, b: float = 0.0):
    """Return a shapely geometry with its lower-left bbox corner at (0, 0)."""
    a, b = float(a), float(b)
    if kind == "circle":
        g = _ellipse(a, a)
    elif kind == "ellipse":
        g = _ellipse(a, b)
    elif kind == "triangle":
        g = Polygon([(0, 0), (a, 0), (a / 2, b)])
    elif kind == "right triangle":
        g = Polygon([(0, 0), (a, 0), (0, b)])
    elif kind == "obround":
        r = min(a, b) / 2
        if a >= b:
            g = LineString([(r, r), (a - r, r)]).buffer(r, quad_segs=_QUAD) if a > b else Point(r, r).buffer(r, quad_segs=_QUAD)
        else:
            g = LineString([(r, r), (r, b - r)]).buffer(r, quad_segs=_QUAD)
    else:  # rectangle (and anything unknown)
        g = box(0, 0, a, b)
    return normalize(g)


def is_rectangle(kind: str, geom=None) -> bool:
    return kind == "rectangle" and geom is None


def geom_to_json(geom, nd: int = 4):
    """Polygon/MultiPolygon -> list of {"ext": [[x,y],..], "holes": [[[x,y],..],..]}."""
    polys = [geom] if geom.geom_type == "Polygon" else [g for g in geom.geoms if g.geom_type == "Polygon"]
    out = []
    for p in polys:
        p = orient(p, 1.0)
        out.append({
            "ext": [[round(x, nd), round(y, nd)] for x, y in p.exterior.coords],
            "holes": [[[round(x, nd), round(y, nd)] for x, y in r.coords] for r in p.interiors],
        })
    return out


def geom_from_json(data):
    polys = [Polygon(d["ext"], d.get("holes") or []) for d in data]
    return polys[0] if len(polys) == 1 else MultiPolygon(polys)


def stored_geometry(outline):
    """Geometry stored with a part ({"polys": [...]}) or None (also for legacy formats)."""
    if isinstance(outline, dict) and outline.get("polys"):
        return geom_from_json(outline["polys"])
    return None


def part_geometry(p: dict):
    """Shapely geometry (origin-normalised) for a part dict from the app/DB."""
    g = stored_geometry(p.get("outline"))
    if g is not None:
        return normalize(g)
    return build_shape(p.get("shape") or "rectangle", p["width"], p["height"])
