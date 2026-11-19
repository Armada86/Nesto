import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
import os, tempfile
import pytest

pytest.importorskip("OCP")
from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse
from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder
from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer
from OCP.gp import gp_Ax1, gp_Dir, gp_Pnt, gp_Trsf, gp_Vec

from step_io import read_step


def to_step(shape) -> bytes:
    w = STEPControl_Writer()
    w.Transfer(shape, STEPControl_AsIs)
    fd, p = tempfile.mkstemp(suffix=".step"); os.close(fd)
    w.Write(p)
    data = open(p, "rb").read(); os.unlink(p)
    return data


def plate_with_holes():
    box = BRepPrimAPI_MakeBox(100, 50, 5).Shape()
    for x in (25, 75):
        cyl = BRepPrimAPI_MakeCylinder(gp_Ax2_at(x, 25), 6, 10).Shape()
        box = BRepAlgoAPI_Cut(box, cyl).Shape()
    return box


def gp_Ax2_at(x, y):
    from OCP.gp import gp_Ax2
    return gp_Ax2(gp_Pnt(x, y, -2), gp_Dir(0, 0, 1))


def test_plate_with_two_holes():
    ((g, note),) = read_step(to_step(plate_with_holes()))
    assert note is None
    assert g.bounds == pytest.approx((0, 0, 100, 50), abs=0.01)
    assert len(g.interiors) == 2
    assert g.area == pytest.approx(100 * 50 - 2 * 3.14159 * 36, rel=0.01)


def test_l_shape_and_tilted():
    l = BRepAlgoAPI_Fuse(BRepPrimAPI_MakeBox(100, 20, 3).Shape(), BRepPrimAPI_MakeBox(20, 80, 3).Shape()).Shape()
    t = gp_Trsf(); t.SetRotation(gp_Ax1(gp_Pnt(0, 0, 0), gp_Dir(1, 1, 0.3)), 0.9)  # arbitrary 3D tilt
    tilted = BRepBuilderAPI_Transform(l, t).Shape()
    ((g, _),) = read_step(to_step(tilted))
    assert g.area == pytest.approx(100 * 20 + 20 * 60, rel=0.01)  # L outline, orientation-independent


def test_two_solids_and_thick_warning():
    cube = BRepPrimAPI_MakeBox(50, 50, 50).Shape()
    res = read_step(to_step(cube))
    assert res[0][1] is not None  # not plate-like
    a = BRepPrimAPI_MakeBox(40, 30, 2).Shape()
    b = BRepBuilderAPI_Transform(BRepPrimAPI_MakeBox(10, 10, 2).Shape(), _move(200, 0, 0)).Shape()
    from OCP.TopoDS import TopoDS_Compound
    from OCP.BRep import BRep_Builder
    comp, bb = TopoDS_Compound(), BRep_Builder()
    bb.MakeCompound(comp); bb.Add(comp, a); bb.Add(comp, b)
    assert len(read_step(to_step(comp))) == 2


def _move(x, y, z):
    t = gp_Trsf(); t.SetTranslation(gp_Vec(x, y, z)); return t


def test_garbage():
    with pytest.raises(ValueError):
        read_step(b"not a step file")
