"""Manual editing of a finished layout: move one part, but never into another part or out of the sheet.

The browser editor (components/sheet_editor) already stops a dragged part at contact; every move it reports is
checked here again with exact geometry (Shapely) before it changes the layout, so a bug or a manipulated request in
the browser can never produce overlapping parts.

Sheets are the plain dicts stored in the run: {"width", "height", "placements": [{"part", "x", "y", "w", "h", ...,
"polys": [{"ext": ..., "holes": ...}]}]}.
"""
from __future__ import annotations

from shapely import affinity
from shapely.geometry import box
from shapely.strtree import STRtree

from shapes import geom_from_json, geom_to_json

TOL = 0.01  # rounding tolerance (outlines are stored with 4 decimals)


def placement_geom(p: dict):
    if p.get("polys"):
        return geom_from_json(p["polys"])
    return box(p["x"], p["y"], p["x"] + p["w"], p["y"] + p["h"])


def ensure_polys(sheet: dict) -> None:
    """Placements from the rectangle engine have no outline; give them one so they can be edited like the others."""
    for p in sheet["placements"]:
        if not p.get("polys"):
            p["polys"] = geom_to_json(placement_geom(p))


def check_position(sheet: dict, index: int, geom, margin: float, spacing: float) -> str | None:
    """None if part `index` may sit at `geom`, otherwise the reason it may not."""
    if not box(margin - TOL, margin - TOL, sheet["width"] - margin + TOL, sheet["height"] - margin + TOL).contains(geom):
        return "outside the sheet margin"
    others = [placement_geom(p) for k, p in enumerate(sheet["placements"]) if k != index]
    if not others:
        return None
    tree = STRtree(others)
    for j in tree.query(geom.buffer(spacing + 1.0)):
        o = others[int(j)]
        if spacing > TOL:
            if geom.distance(o) < spacing - TOL:
                return "too close to another part"
        elif geom.intersection(o).area > 1e-6:
            return "overlaps another part"
    return None


def apply_move(sheet: dict, index: int, dx: float, dy: float, margin: float, spacing: float):
    """Translate part `index` by (dx, dy). Returns (ok, message); the sheet is only changed when ok."""
    pl = sheet["placements"]
    if not (0 <= index < len(pl)):
        return False, "unknown part"
    if not all(isinstance(v, (int, float)) and abs(v) < 1e6 for v in (dx, dy)):
        return False, "invalid move"
    g = affinity.translate(placement_geom(pl[index]), dx, dy)
    reason = check_position(sheet, index, g, margin, spacing)
    if reason:
        return False, reason
    p = pl[index]
    x0, y0, x1, y1 = g.bounds
    p["polys"] = geom_to_json(g)
    p["x"], p["y"], p["w"], p["h"] = x0, y0, x1 - x0, y1 - y0
    return True, "moved"


def apply_transform(sheet: dict, index: int, op: str, value, ox: float, oy: float, nx: float, ny: float,
                    margin: float, spacing: float):
    """Rotate (op "rotate", value = degrees counter-clockwise) or mirror (op "flip", value "h" = left-right,
    "v" = top-bottom) part `index` (rotate: about its centre of mass, flip: about the middle of its bounding box), in place.

    The part is shifted by (ox, oy) first (a drag the browser has not reported yet), turned, then shifted by (nx, ny)
    (used to bring a turned part at the sheet edge back inside the margin). Returns (ok, message); the sheet is only
    changed when ok, i.e. when the turned part has room: inside the margin and not too close to any other part."""
    pl = sheet["placements"]
    if not (0 <= index < len(pl)):
        return False, "unknown part"
    if not all(isinstance(v, (int, float)) and abs(v) < 1e6 for v in (ox, oy, nx, ny)):
        return False, "invalid move"
    g = affinity.translate(placement_geom(pl[index]), ox, oy)
    cx, cy = (g.bounds[0] + g.bounds[2]) / 2, (g.bounds[1] + g.bounds[3]) / 2     # flip: the middle of the bounding box
    if op == "rotate":
        cx, cy = g.centroid.x, g.centroid.y                                        # rotate: the centre of mass (turning back returns)
        if not isinstance(value, (int, float)) or abs(value) > 720:
            return False, "invalid angle"
        g = affinity.rotate(g, value, origin=(cx, cy))
    elif op == "flip":
        if value not in ("h", "v"):
            return False, "invalid flip"
        g = affinity.scale(g, xfact=-1 if value == "h" else 1, yfact=-1 if value == "v" else 1, origin=(cx, cy))
    else:
        return False, "unknown operation"
    g = affinity.translate(g, nx, ny)
    reason = check_position(sheet, index, g, margin, spacing)
    if reason:
        return False, reason
    p = pl[index]
    x0, y0, x1, y1 = g.bounds
    p["polys"] = geom_to_json(g)
    p["x"], p["y"], p["w"], p["h"] = x0, y0, x1 - x0, y1 - y0
    if op == "rotate":
        p["angle"] = (float(p.get("angle", 0.0)) + value) % 360
    else:
        p["mirrored"] = not p.get("mirrored", False)
    return True, "turned"


def sheet_problems(sheet: dict, margin: float, spacing: float) -> list[str]:
    """Every rule violation in a sheet (for tests and sanity checks): [] means a valid layout."""
    out = []
    for i, p in enumerate(sheet["placements"]):
        reason = check_position(sheet, i, placement_geom(p), margin, spacing)
        if reason:
            out.append(f"{p['part']} (#{i}): {reason}")
    return out
