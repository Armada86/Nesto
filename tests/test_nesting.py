import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
import pytest
from nesting import Part, nest, summary


def overlaps(a, b):
    return a.x < b.x + b.w - 1e-6 and b.x < a.x + a.w - 1e-6 and a.y < b.y + b.h - 1e-6 and b.y < a.y + a.h - 1e-6


def check(sheets, sw, sh, margin=0.0):
    for s in sheets:
        for i, p in enumerate(s.placements):
            assert p.x >= margin - 1e-6 and p.y >= margin - 1e-6
            assert p.x + p.w <= sw - margin + 1e-6 and p.y + p.h <= sh - margin + 1e-6
            for q in s.placements[i + 1:]:
                assert not overlaps(p, q)


def test_perfect_fit():
    s = nest([Part("a", 50, 50, 4)], 100, 100)
    assert len(s) == 1 and summary(s)["utilization"] == pytest.approx(1.0)


def test_rotation_needed():
    s = nest([Part("a", 100, 10, 1)], 20, 120)
    assert len(s) == 1 and s[0].placements[0].rotated


def test_no_rotation_fails():
    with pytest.raises(ValueError):
        nest([Part("a", 100, 10, 1, rotate=False)], 20, 120)


def test_margin_spacing_no_overlap():
    parts = [Part("a", 37, 21, 15), Part("b", 55, 13, 10), Part("c", 12, 12, 30)]
    s = nest(parts, 200, 120, margin=5, spacing=2)
    check(s, 200, 120, 5)
    assert sum(len(x.placements) for x in s) == 55


def test_multi_sheet():
    s = nest([Part("a", 60, 60, 5)], 100, 100)
    assert len(s) == 5


def test_cut_length_is_the_border_of_the_parts_including_holes():
    from nesting import Placement, Sheet, summary
    from shapely.geometry import box
    from shapes import geom_to_json
    rect = Placement("r", 0, 0, 300, 200, False)                              # rectangle engine: 2 * (300 + 200)
    g = box(400, 0, 700, 300).difference(box(450, 50, 550, 150))              # 300 x 300 plate with a 100 x 100 hole
    plate = Placement("p", 400, 0, 300, 300, False, 0.0, g.area, geom_to_json(g))
    assert rect.cut_length == 1000.0
    assert abs(plate.cut_length - (1200 + 400)) < 1e-6
    s1, s2 = Sheet(1000, 500, [rect, plate]), Sheet(1000, 500, [rect])
    assert abs(s1.cut_length - 2600) < 1e-6 and s2.cut_length == 1000.0
    assert abs(summary([s1, s2])["cut_length"] - 3600) < 1e-6 and summary([])["cut_length"] == 0.0
