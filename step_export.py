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


def _write(named_shapes) -> bytes:
    """named_shapes: [(name, TopoDS_Shape)] -> bytes of a STEP file with one named entity per shape."""
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.STEPCAFControl import STEPCAFControl_Writer
    from OCP.STEPControl import STEPControl_AsIs
    from OCP.TCollection import TCollection_ExtendedString
    from OCP.TDataStd import TDataStd_Name
    from OCP.TDocStd import TDocStd_Document
    from OCP.XCAFDoc import XCAFDoc_DocumentTool

    doc = TDocStd_Document(TCollection_ExtendedString("MDTV-XCAF"))
    tool = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
    for name, shape in named_shapes:
        label = tool.AddShape(shape, False)
        TDataStd_Name.Set_s(label, TCollection_ExtendedString(name))
    writer = STEPCAFControl_Writer()
    writer.SetNameMode(True)
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


SHEET_PLATE = 1.0   # thickness of the sheet plate (mm): its TOP face is the plane z = 0 where the parts start


def _sheet_entities(sheet, number, dx=0.0, dy=0.0, thickness=0.0):
    """The sheet of a STEP file: a solid rectangular plate of the sheet size, 1 mm thick, below the parts (its upper
    face is the plane z = 0 where the parts start; the plate spans z = -1 .. 0)."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
    from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
    from OCP.gp import gp_Vec

    _ocp()
    corners = [(dx, dy), (dx + sheet.width, dy), (dx + sheet.width, dy + sheet.height), (dx, dy + sheet.height)]
    face = BRepBuilderAPI_MakeFace(_wire(corners, z=-SHEET_PLATE), True).Face()
    items = [(f"Sheet {number}", BRepPrimAPI_MakePrism(face, gp_Vec(0, 0, SHEET_PLATE)).Shape())]
    for p in sheet.placements:
        items.append((p.part, _part_shape(p, dx, dy, thickness)))
    return items


def sheet_step(sheet, number=1, thickness=0.0) -> bytes:
    return _write(_sheet_entities(sheet, number, thickness=thickness))


def all_sheets_step(sheets, gap=300.0, thickness=0.0) -> bytes:
    """One STEP with the sheets stacked vertically (sheet 1 on top), like the stacked DXF."""
    items, y = [], 0.0
    for i, s in enumerate(sheets, 1):
        dy = y - s.height
        items += _sheet_entities(s, i, dy=dy, thickness=thickness)
        y = dy - gap
    return _write(items)


def sheets_step_zip(sheets, prefix="sheet", thickness=0.0) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for i, s in enumerate(sheets, 1):
            z.writestr(f"{prefix}_{i:02d}.step", sheet_step(s, i, thickness))
    return buf.getvalue()
