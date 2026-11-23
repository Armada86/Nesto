"""Export nested sheets to STEP (one file per sheet, or all sheets stacked in one file).

Every part becomes a named flat face (outer outline + holes) in the XY plane - or, with a thickness, a solid plate
extruded in +Z. The sheet is added as an empty rectangle of zero thickness (a hairline flat frame, 0.05 mm, whose inner edge is the sheet outline) named "Sheet N". Needs the 'cadquery-ocp' package (the same one
that reads STEP files).
"""
from __future__ import annotations

import io
import os
import tempfile
import zipfile

from layout_edit import placement_geom
from shapes import normalize  # noqa: F401  (kept for symmetry with step_io)


def _ocp():
    try:
        import OCP  # noqa: F401
    except ImportError as e:  # pragma: no cover
        raise ValueError("STEP export needs the 'cadquery-ocp' package (pip install cadquery-ocp)") from e


def _wire(points, reverse=False, z=0.0):
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakePolygon
    from OCP.gp import gp_Pnt

    pts = list(points)
    if len(pts) > 1 and tuple(pts[0]) == tuple(pts[-1]):
        pts = pts[:-1]
    if reverse:
        pts = pts[::-1]
    mk = BRepBuilderAPI_MakePolygon()
    for x, y in pts:
        mk.Add(gp_Pnt(float(x), float(y), float(z)))
    mk.Close()
    return mk.Wire()


def _faces(geom, dx, dy):
    """Planar faces (outer ring CCW, holes CW) of a shapely geometry shifted by (dx, dy)."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
    from shapely import affinity
    from shapely.geometry.polygon import orient

    g = affinity.translate(geom, dx, dy)
    polys = [g] if g.geom_type == "Polygon" else [x for x in g.geoms if x.geom_type == "Polygon"]
    out = []
    for poly in polys:
        poly = orient(poly, 1.0)
        mk = BRepBuilderAPI_MakeFace(_wire(poly.exterior.coords), True)
        for ring in poly.interiors:
            mk.Add(_wire(ring.coords))
        if mk.IsDone():
            out.append(mk.Face())
    return out


def _compound(shapes):
    from OCP.BRep import BRep_Builder
    from OCP.TopoDS import TopoDS_Compound

    comp, b = TopoDS_Compound(), BRep_Builder()
    b.MakeCompound(comp)
    for s in shapes:
        b.Add(comp, s)
    return comp


def _part_shape(p, dx, dy, thickness):
    from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
    from OCP.gp import gp_Vec

    faces = _faces(placement_geom(_as_dict(p)), dx, dy)
    if thickness and thickness > 0:
        faces = [BRepPrimAPI_MakePrism(f, gp_Vec(0, 0, float(thickness))).Shape() for f in faces]
    return _compound(faces)


def _as_dict(p):
    """A Placement (dataclass) as the plain dict layout_edit works with."""
    return {"x": p.x, "y": p.y, "w": p.w, "h": p.h, "polys": p.polys}


# the sheet is a neutral light grey-blue, the parts use saturated colours: no part can look like the sheet
SHEET_COLOR = (0.80, 0.82, 0.86)
PART_COLORS = [(0.86, 0.25, 0.22), (0.96, 0.60, 0.15), (0.95, 0.85, 0.20), (0.30, 0.70, 0.30), (0.15, 0.65, 0.65),
               (0.20, 0.45, 0.85), (0.55, 0.35, 0.80), (0.85, 0.30, 0.65), (0.60, 0.40, 0.25), (0.65, 0.80, 0.25)]


def assign_colors(placements, touch=6.0) -> list:
    """A colour index (into PART_COLORS) for every placement of one sheet: parts that touch (closer than `touch`)
    never share a colour; parts with the same name prefer the same colour when that is allowed."""
    from shapely.strtree import STRtree

    geoms = [placement_geom(_as_dict(p)) for p in placements]
    n = len(geoms)
    tree = STRtree(geoms) if n else None
    near = [set() for _ in range(n)]
    for i, g in enumerate(geoms):
        for j in tree.query(g.buffer(touch)):
            j = int(j)
            if j != i and g.distance(geoms[j]) <= touch:
                near[i].add(j)
                near[j].add(i)
    colour = [None] * n
    by_name = {}
    # most constrained first (largest number of touching neighbours), so a few colours are enough
    for i in sorted(range(n), key=lambda k: (-len(near[k]), k)):
        taken = {colour[j] for j in near[i] if colour[j] is not None}
        pref = by_name.get(placements[i].part)
        if pref is not None and pref not in taken:
            c = pref
        else:
            free = [k for k in range(len(PART_COLORS)) if k not in taken]
            c = free[0] if free else min(range(len(PART_COLORS)), key=lambda k: sum(1 for j in near[i] if colour[j] == k))
        colour[i] = c
        by_name.setdefault(placements[i].part, c)
    return colour


def _write(named_shapes) -> bytes:
    """named_shapes: [(name, TopoDS_Shape, (r, g, b))] -> bytes of a STEP file with one named, coloured entity per shape."""
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.STEPCAFControl import STEPCAFControl_Writer
    from OCP.STEPControl import STEPControl_AsIs
    from OCP.TCollection import TCollection_ExtendedString
    from OCP.TDataStd import TDataStd_Name
    from OCP.TDocStd import TDocStd_Document
    from OCP.Quantity import Quantity_Color, Quantity_TOC_sRGB
    from OCP.XCAFDoc import XCAFDoc_ColorType, XCAFDoc_DocumentTool

    doc = TDocStd_Document(TCollection_ExtendedString("MDTV-XCAF"))
    tool = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
    colors = XCAFDoc_DocumentTool.ColorTool_s(doc.Main())
    for name, shape, rgb in named_shapes:
        label = tool.AddShape(shape, False)
        TDataStd_Name.Set_s(label, TCollection_ExtendedString(name))
        colors.SetColor(label, Quantity_Color(float(rgb[0]), float(rgb[1]), float(rgb[2]), Quantity_TOC_sRGB),
                        XCAFDoc_ColorType.XCAFDoc_ColorSurf)
    writer = STEPCAFControl_Writer()
    writer.SetNameMode(True)
    writer.SetColorMode(True)
    if not writer.Transfer(doc, STEPControl_AsIs):
        raise ValueError("Cannot build the STEP file")
    fd, path = tempfile.mkstemp(suffix=".step")
    os.close(fd)
    try:
        if writer.Write(path) != IFSelect_RetDone:
            raise ValueError("Cannot write the STEP file")
        with open(path, "rb") as f:
            return f.read()
    finally:
        os.unlink(path)


SHEET_PLATE = 0.5   # thickness of the sheet plate (mm): its TOP face is the plane z = 0 where the parts start


def _sheet_entities(sheet, number, dx=0.0, dy=0.0, thickness=0.0, touch=6.0):
    """The sheet of a STEP file: a solid rectangular plate of the sheet size, 0.5 mm thick, below the parts (its upper
    face is the plane z = 0 where the parts start; the plate spans z = -0.5 .. 0)."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
    from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
    from OCP.gp import gp_Vec

    _ocp()
    corners = [(dx, dy), (dx + sheet.width, dy), (dx + sheet.width, dy + sheet.height), (dx, dy + sheet.height)]
    face = BRepBuilderAPI_MakeFace(_wire(corners, z=-SHEET_PLATE), True).Face()
    items = [(f"Sheet {number}", BRepPrimAPI_MakePrism(face, gp_Vec(0, 0, SHEET_PLATE)).Shape(), SHEET_COLOR)]
    for p, c in zip(sheet.placements, assign_colors(sheet.placements, touch)):
        items.append((p.part, _part_shape(p, dx, dy, thickness), PART_COLORS[c]))
    return items


def sheet_step(sheet, number=1, thickness=0.0, touch=6.0) -> bytes:
    return _write(_sheet_entities(sheet, number, thickness=thickness, touch=touch))


def all_sheets_step(sheets, gap=300.0, thickness=0.0, touch=6.0) -> bytes:
    """One STEP with the sheets stacked vertically (sheet 1 on top), like the stacked DXF."""
    items, y = [], 0.0
    for i, s in enumerate(sheets, 1):
        dy = y - s.height
        items += _sheet_entities(s, i, dy=dy, thickness=thickness, touch=touch)
        y = dy - gap
    return _write(items)


def sheets_step_zip(sheets, prefix="sheet", thickness=0.0, touch=6.0) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for i, s in enumerate(sheets, 1):
            z.writestr(f"{prefix}_{i:02d}.step", sheet_step(s, i, thickness, touch))
    return buf.getvalue()
