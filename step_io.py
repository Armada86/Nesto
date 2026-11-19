"""Read flat (plate-like) parts from STEP files.

For every solid in the file the largest planar face is taken as the part's flat
face: its outer wire is the outline and its inner wires are holes. Thickness is
not needed. Bent / formed parts are not supported - export their flat pattern
(DXF) from the CAD system instead.
"""
from __future__ import annotations

import os
import tempfile

from shapely import affinity
from shapely.geometry import Polygon

from shapes import normalize

DEFLECTION = 0.05  # max distance between a curve and its polyline (in file units)


def _ocp():
    try:
        import OCP  # noqa: F401
    except ImportError as e:  # pragma: no cover
        raise ValueError("STEP support needs the 'cadquery-ocp' package (pip install cadquery-ocp)") from e


def _read_shape(data: bytes):
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.STEPControl import STEPControl_Reader

    fd, path = tempfile.mkstemp(suffix=".step")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        reader = STEPControl_Reader()
        if reader.ReadFile(path) != IFSelect_RetDone:
            raise ValueError("Cannot read STEP file")
        reader.TransferRoots()
        shape = reader.OneShape()
    finally:
        os.unlink(path)
    if shape.IsNull():
        raise ValueError("The STEP file contains no geometry")
    return shape


def _solids(shape):
    from OCP.TopAbs import TopAbs_SOLID
    from OCP.TopExp import TopExp_Explorer

    out = []
    ex = TopExp_Explorer(shape, TopAbs_SOLID)
    while ex.More():
        out.append(ex.Current())
        ex.Next()
    return out or [shape]


def _unify(solid):
    """Merge coplanar faces that a CAD fuse/cut left split in several pieces."""
    from OCP.ShapeUpgrade import ShapeUpgrade_UnifySameDomain

    u = ShapeUpgrade_UnifySameDomain(solid, True, True, True)
    u.Build()
    return u.Shape()


def _largest_planar_face(solid):
    from OCP.BRep import BRep_Tool
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.BRepGProp import BRepGProp
    from OCP.GeomAbs import GeomAbs_Plane
    from OCP.GProp import GProp_GProps
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    best, best_area = None, 0.0
    ex = TopExp_Explorer(solid, TopAbs_FACE)
    while ex.More():
        face = TopoDS.Face(ex.Current())
        if BRepAdaptor_Surface(face).GetType() == GeomAbs_Plane:
            props = GProp_GProps()
            BRepGProp.SurfaceProperties_s(face, props)
            if props.Mass() > best_area:
                best, best_area = face, props.Mass()
        ex.Next()
    if best is None:
        raise ValueError("No planar face found - only flat (plate-like) parts are supported")
    return best


def _wire_points(wire, to_2d):
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.BRepTools import BRepTools_WireExplorer
    from OCP.GCPnts import GCPnts_QuasiUniformDeflection
    from OCP.TopAbs import TopAbs_REVERSED

    pts = []
    we = BRepTools_WireExplorer(wire)
    while we.More():
        edge = we.Current()
        curve = BRepAdaptor_Curve(edge)
        d = GCPnts_QuasiUniformDeflection(curve, DEFLECTION)
        seg = [to_2d(d.Value(i + 1)) for i in range(d.NbPoints())] if d.IsDone() else \
            [to_2d(curve.Value(curve.FirstParameter())), to_2d(curve.Value(curve.LastParameter()))]
        if edge.Orientation() == TopAbs_REVERSED:
            seg.reverse()
        pts += seg[1:] if pts else seg
        we.Next()
    return pts


def _face_polygon(face):
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.BRepTools import BRepTools
    from OCP.TopAbs import TopAbs_REVERSED, TopAbs_WIRE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS
    from OCP.gp import gp_Vec

    ax = BRepAdaptor_Surface(face).Plane().Position()
    o, xd, yd = ax.Location(), ax.XDirection(), ax.YDirection()
    flip = -1.0 if face.Orientation() == TopAbs_REVERSED else 1.0  # view the face from outside

    def to_2d(p):
        v = gp_Vec(o, p)
        return (flip * v.Dot(gp_Vec(xd)), v.Dot(gp_Vec(yd)))

    outer = BRepTools.OuterWire_s(face)
    rings, holes = _wire_points(outer, to_2d), []
    ex = TopExp_Explorer(face, TopAbs_WIRE)
    while ex.More():
        w = TopoDS.Wire(ex.Current())
        if not w.IsSame(outer):
            holes.append(_wire_points(w, to_2d))
        ex.Next()
    poly = Polygon(rings, [h for h in holes if len(h) >= 3])
    if not poly.is_valid:
        poly = poly.buffer(0)
    return poly, ax


def _thickness(solid, ax):
    from OCP.TopAbs import TopAbs_VERTEX
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS
    from OCP.BRep import BRep_Tool
    from OCP.gp import gp_Vec

    n = gp_Vec(ax.Direction())
    o = ax.Location()
    ds = []
    ex = TopExp_Explorer(solid, TopAbs_VERTEX)
    while ex.More():
        p = BRep_Tool.Pnt_s(TopoDS.Vertex(ex.Current()))
        ds.append(gp_Vec(o, p).Dot(n))
        ex.Next()
    return max(ds) - min(ds) if ds else 0.0


def read_step(data: bytes, scale: float = 1.0):
    """Return a list of (shapely_geometry, note) - one entry per solid in the STEP file.

    Geometry has its bbox lower-left at (0, 0). `note` is a warning string or None.
    """
    _ocp()
    shape = _read_shape(data)
    out = []
    for solid in _solids(shape):
        solid = _unify(solid)
        face = _largest_planar_face(solid)
        poly, ax = _face_polygon(face)
        if poly.is_empty or poly.area <= 0:
            raise ValueError("Could not build an outline from the largest planar face")
        thick = _thickness(solid, ax)
        w, h = poly.bounds[2] - poly.bounds[0], poly.bounds[3] - poly.bounds[1]
        note = None
        if thick > 0.5 * min(w, h):
            note = (f"thickness {thick:.1f} is large compared with the {w:.0f} x {h:.0f} outline - "
                    "is this a flat part? Bent/formed parts need their flat pattern (DXF)")
        if scale != 1.0:
            poly = affinity.scale(poly, scale, scale, origin=(0, 0))
        out.append((normalize(poly), note))
    return out
