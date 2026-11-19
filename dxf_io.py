"""Read part geometry (any closed outline, with holes) from DXF drawings."""
from __future__ import annotations

import io

import ezdxf
import shapely
from ezdxf.path import make_path
from shapely import affinity
from shapely.geometry import LineString, Polygon
from shapely.ops import polygonize, unary_union

from shapes import normalize

SUPPORTED = {"LINE", "ARC", "CIRCLE", "ELLIPSE", "LWPOLYLINE", "POLYLINE", "SPLINE"}


def _linework(entities, flatten, depth=0):
    lines = []
    for e in entities:
        t = e.dxftype()
        if t == "INSERT" and depth < 5:
            try:
                lines += _linework(e.virtual_entities(), flatten, depth + 1)
            except Exception:  # noqa: BLE001
                pass
        elif t in SUPPORTED:
            try:
                pts = [(v.x, v.y) for v in make_path(e).flattening(flatten)]
            except Exception:  # noqa: BLE001
                continue
            if len(pts) >= 2:
                lines.append(pts)
    return lines


def _faces(lines, tol):
    merged = unary_union([LineString(p) for p in lines])
    merged = shapely.set_precision(merged, tol)
    return list(polygonize(list(getattr(merged, "geoms", [merged]))))


def read_dxf(data: bytes, one_part: bool = True, scale: float = 1.0, flatten: float = 0.05, tol: float = 1e-3):
    """Return a list of shapely geometries (bbox lower-left at 0,0) found in a DXF file.

    Closed contours become polygons; contours inside other contours become holes
    (even/odd nesting). one_part=True merges everything into a single part,
    otherwise every top-level contour is a separate part.
    """
    try:
        doc = ezdxf.read(io.StringIO(data.decode("utf-8", errors="ignore")))
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"Cannot read DXF: {e}") from e
    lines = _linework(doc.modelspace(), flatten)
    if not lines:
        raise ValueError("No supported geometry found (LINE, ARC, CIRCLE, POLYLINE, SPLINE...)")
    faces = [f for f in _faces(lines, tol) if f.area > tol ** 2]
    if not faces:
        raise ValueError("No closed contour found - the outline must be a closed shape")
    fills = [Polygon(f.exterior) for f in faces]
    keep = []
    for i, f in enumerate(faces):
        pt = f.representative_point()
        depth = sum(1 for j, g in enumerate(fills) if j != i and g.contains(pt))
        if depth % 2 == 0:
            keep.append(f)
    if one_part:
        geoms = [unary_union(keep)]
    else:
        geoms = keep
    out = []
    for g in geoms:
        if scale != 1.0:
            g = affinity.scale(g, scale, scale, origin=(0, 0))
        out.append(normalize(g))
    return out
