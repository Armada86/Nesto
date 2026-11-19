import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
import io, time
import ezdxf
import pytest
from shapely.geometry import Polygon, box
from shapely.ops import unary_union

from dxf_io import read_dxf
from nesting import summary
from nesting_poly import PolyPart, nest_polygons
from shapes import build_shape, geom_from_json, geom_to_json


def placed_geoms(sheet):
    return [geom_from_json(p.polys) for p in sheet.placements]


def check(sheets, sw, sh, margin, spacing):
    area = box(margin, margin, sw - margin, sh - margin)
    for s in sheets:
        gs = placed_geoms(s)
        for i, g in enumerate(gs):
            assert area.buffer(1e-6).contains(g)
            for h in gs[i + 1:]:
                assert g.distance(h) >= spacing - 1e-6, (g.distance(h), spacing)


def test_mixed_shapes_no_overlap_and_spacing():
    parts = [PolyPart("circ", build_shape("circle", 40), 6), PolyPart("tri", build_shape("triangle", 50, 60), 6),
             PolyPart("rt", build_shape("right triangle", 70, 30), 5), PolyPart("ell", build_shape("ellipse", 80, 30), 4),
             PolyPart("slot", build_shape("obround", 60, 15), 5)]
    s = nest_polygons(parts, 400, 250, margin=5, spacing=3, rot_step=15, cell=2.0)
    assert sum(len(x.placements) for x in s) == 26
    check(s, 400, 250, 5, 3)


def test_right_triangles_pair_up():
    parts = [PolyPart("t", build_shape("right triangle", 50, 50), 2)]
    s = nest_polygons(parts, 60, 60, rot_step=90, cell=0.5)
    assert len(s) == 1
    check(s, 60, 60, 0, 0)


def test_part_goes_into_hole():
    ring = box(0, 0, 100, 100).difference(box(20, 20, 80, 80))  # frame with a big hole
    parts = [PolyPart("frame", ring, 1), PolyPart("plug", build_shape("circle", 50), 1)]
    s = nest_polygons(parts, 104, 104, rot_step=90, cell=1.0)
    assert len(s) == 1 and len(s[0].placements) == 2
    check(s, 104, 104, 0, 0)


def test_does_not_fit():
    with pytest.raises(ValueError):
        nest_polygons([PolyPart("big", build_shape("circle", 200), 1)], 100, 100)


def test_dxf_holes_and_split():
    d = ezdxf.new(); m = d.modelspace()
    m.add_lwpolyline([(10, 10), (110, 10), (110, 60), (10, 60)], close=True)
    m.add_circle((60, 35), 10)                                   # hole
    m.add_line((200, 0), (260, 0)); m.add_line((260, 0), (230, 50)); m.add_line((230, 50), (200, 0))  # loose-line triangle
    buf = io.StringIO(); d.write(buf)
    one = read_dxf(buf.getvalue().encode())
    assert len(one) == 1
    many = read_dxf(buf.getvalue().encode(), one_part=False)
    assert len(many) == 2
    plate = max(many, key=lambda g: g.area)
    assert len(plate.interiors) == 1 and plate.bounds == pytest.approx((0, 0, 100, 50))
    assert plate.area == pytest.approx(100 * 50 - 3.14159 * 100, rel=0.01)


def test_json_roundtrip():
    g = box(0, 0, 10, 10).difference(box(2, 2, 4, 4))
    assert geom_from_json(geom_to_json(g)).area == pytest.approx(g.area)


def test_speed_typical_job():
    parts = [PolyPart("c", build_shape("circle", 120), 12), PolyPart("t", build_shape("triangle", 200, 150), 12),
             PolyPart("r", build_shape("rectangle", 300, 100), 8), PolyPart("e", build_shape("ellipse", 250, 90), 8)]
    t = time.time()
    s = nest_polygons(parts, 2500, 1250, margin=10, spacing=5, rot_step=45)
    dt = time.time() - t
    print("time", dt, summary(s))
    check(s, 2500, 1250, 10, 5)
    assert dt < 60
