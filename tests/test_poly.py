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


def test_right_triangles_with_slot_pair_hypotenuse_to_hypotenuse():
    """Regression: triangles used to be placed in a sawtooth (5+5) instead of mated pairs (6+4)."""
    tri = Polygon([(0, 0), (815, 0), (815, 800)]).difference(box(660, 700, 700, 810))
    from shapes import normalize
    s = nest_polygons([PolyPart("H", normalize(tri), 10)], 2500, 1250, margin=5, spacing=3, rot_step=90)
    check(s, 2500, 1250, 5, 3)
    assert [len(x.placements) for x in s] == [6, 4]
    # pairs form near-rectangles: the first sheet is used up to ~2450 mm in length
    assert max(p.x + p.w for p in s[0].placements) < 2480


def test_pair_of_pairs_members_are_consistent():
    """4-up clusters must unpack into correctly placed, non-overlapping parts of the right shape."""
    tri = build_shape("right triangle", 300, 200)
    s = nest_polygons([PolyPart("t", tri, 8)], 1000, 700, margin=5, spacing=3, rot_step=45)
    check(s, 1000, 700, 5, 3)
    assert sum(len(x.placements) for x in s) == 8
    for sh in s:
        for p in sh.placements:
            assert p.area == pytest.approx(0.5 * 300 * 200, rel=0.01)


def test_search_never_worse_than_baseline_and_valid():
    parts = [PolyPart("c", build_shape("circle", 120), 8), PolyPart("t", build_shape("triangle", 200, 150), 8),
             PolyPart("r", build_shape("rectangle", 300, 100), 6)]
    base = nest_polygons(parts, 900, 600, 5, 3, 90, time_limit=0)
    info = {}
    better = nest_polygons(parts, 900, 600, 5, 3, 90, time_limit=4, info=info)
    check(better, 900, 600, 5, 3)
    assert info["runs"] >= 2
    assert (len(better), better[-1].used_area) <= (len(base), base[-1].used_area)


def test_edge_aligned_rotation_lays_hypotenuse_flat():
    """Regression: a strip only ~590 high fits right triangles (legs 756 x 800) with the hypotenuse
    horizontal (needs the part's own edge angle, not a multiple of the 90 degree step)."""
    from shapes import normalize
    tri = normalize(Polygon([(0, 800), (756, 800), (756, 0)]))
    s = nest_polygons([PolyPart("H", tri, 3)], 2500, 600, margin=5, spacing=3, rot_step=90)
    check(s, 2500, 600, 5, 3)
    assert [len(x.placements) for x in s] == [3]


def test_unfittable_part_fails_fast():
    t = time.time()
    with pytest.raises(ValueError):
        nest_polygons([PolyPart("big", build_shape("rectangle", 5000, 5000), 1)], 2500, 1250)
    assert time.time() - t < 2


def test_largest_rect_finder():
    import numpy as np
    from nesting_poly import _largest_rect
    free = np.ones((6, 10), dtype=bool)
    free[2:4, 3:5] = False                       # a block in the middle
    area, x0, y0, x1, y1 = _largest_rect(free)
    assert area == 6 * 5 and (x0, x1) == (5, 10) and (y0, y1) == (0, 6)   # right-hand 5 x 6 block wins


def test_free_rectangles_of_finished_sheet():
    from nesting import Placement, Sheet
    from nesting_poly import free_rectangles
    sh = Sheet(1000, 500, [Placement("a", 0, 0, 600, 500, False)])      # 600 wide part: 400 x 500 free
    (x, y, w, h), *_ = free_rectangles(sh, margin=0)
    assert w == pytest.approx(400, abs=25) and h == pytest.approx(500, abs=25) and x == pytest.approx(600, abs=25)


def test_small_parts_go_into_gaps_and_keep_the_big_strip_clean():
    """Sheet 1 has a big plate and a free strip; sheet 2 has a frame with a pocket (hole).
    The small parts must go into the pocket, not into sheet 1's strip."""
    from nesting_poly import free_rectangles
    plate = box(0, 0, 986, 400)                                              # nearly full width: no side column
    frame = box(0, 0, 986, 420).difference(box(300, 100, 500, 300))      # 200 x 200 pocket
    parts = [PolyPart("plate", plate, 1), PolyPart("frame", frame, 1), PolyPart("small", box(0, 0, 40, 40), 4, rotate=False)]
    s = nest_polygons(parts, 1000, 500, margin=5, spacing=3, rot_step=90, time_limit=0)
    check(s, 1000, 500, 5, 3)
    assert len(s) == 2
    names = [sorted(p.part for p in sh.placements) for sh in s]
    sheet_of = {n: i for i, ns in enumerate(names) for n in ns if n != "small"}
    small_sheet = [i for i, ns in enumerate(names) if "small" in ns]
    assert small_sheet == [sheet_of["frame"]]                              # all 4 smalls on the frame's sheet
    # (the frame's own 62 mm strip is thinner than 15 % of the sheet: waste, so the smalls may use it)
    # the plate's sheet keeps its full-length free strip
    (x, y, w, h), *_ = free_rectangles(s[sheet_of["plate"]], margin=5)
    assert w > 980 and h >= 75


def test_scrap_never_costs_a_sheet():
    parts = [PolyPart("c", build_shape("circle", 120), 10), PolyPart("t", build_shape("triangle", 200, 150), 10),
             PolyPart("r", build_shape("rectangle", 300, 100), 6)]
    s = nest_polygons(parts, 900, 600, 5, 3, 90, time_limit=0)
    check(s, 900, 600, 5, 3)
    assert sum(len(x.placements) for x in s) == 26


def test_small_parts_prefer_irregular_gaps_over_a_regular_strip():
    """Regression (screenshot): small parts sat in the top-left corner of sheet 1, cutting its long strip,
    although sheet 2 had a wedge (leftover of a diagonal-edged part) where they fit.
    Regular scrap = rectangular, touches the sheet edge. The wedge is not regular, so smalls go there."""
    from nesting_poly import free_rectangles
    plate = box(0, 0, 580, 300)
    wedge_maker = Polygon([(0, 0), (580, 0), (0, 300)])                       # right triangle: leaves a wedge
    smalls = PolyPart("small", box(0, 0, 30, 30), 5, rotate=False)
    s = nest_polygons([PolyPart("plate", plate, 1), PolyPart("tri", wedge_maker, 1, rotate=False), smalls],
                      600, 400, margin=5, spacing=3, rot_step=90, time_limit=0)
    check(s, 600, 400, 5, 3)
    assert len(s) == 2
    plate_sheet = next(i for i, sh in enumerate(s) if any(p.part == "plate" for p in sh.placements))
    assert not any(p.part == "small" for p in s[plate_sheet].placements)       # the plate's sheet keeps its strip clean
    (x, y, w, h), *_ = free_rectangles(s[plate_sheet], margin=5)
    assert w > 570 and h >= 80                                                  # ... full length


def test_enclosed_pocket_and_irregular_region_are_not_regular_scrap():
    from nesting import Placement, Sheet
    from nesting_poly import free_rectangles
    # a frame leaves an enclosed hole (not scrap) but also an edge strip (scrap)
    g = box(0, 0, 900, 300).difference(box(300, 80, 500, 220))
    sh = Sheet(1000, 500, [Placement("frame", 5, 5, 900, 300, False, 0.0, g.area,
                                      __import__("shapes").geom_to_json(__import__("shapely").affinity.translate(g, 5, 5)))])
    rects = free_rectangles(sh, margin=5)
    assert rects and all(not (300 < x < 520 and 80 < y < 240 and w < 250) for x, y, w, h in rects)   # hole is not offered


def test_squarer_scrap_wins_over_bigger_thin_strip():
    from nesting import Placement, Sheet
    from nesting_poly import _fitness, scrap_score, squareness
    assert squareness(400, 400) == 1.0 and squareness(1000, 100) == pytest.approx(0.1)
    strip = Sheet(1000, 500, [Placement("p", 0, 0, 10, 10, False)]); strip.scrap = [(0, 0, 1000, 200)]    # 200k, ratio 5:1
    block = Sheet(1000, 500, [Placement("p", 0, 0, 10, 10, False)]); block.scrap = [(0, 0, 400, 400)]     # 160k, square
    assert scrap_score(400, 400) > scrap_score(1000, 200)
    assert _fitness([block]) < _fitness([strip])              # smaller key = better: the square block wins


def test_unavoidable_strip_is_kept_whole_as_tie_break():
    """Same squareness class: the layout whose biggest strip is longer wins (the strip stays whole)."""
    from nesting import Placement, Sheet
    from nesting_poly import _fitness
    whole = Sheet(1000, 500, [Placement("p", 0, 0, 10, 10, False)]); whole.scrap = [(0, 0, 1000, 100)]
    cut = Sheet(1000, 500, [Placement("p", 0, 0, 10, 10, False)]); cut.scrap = [(0, 0, 700, 100)]
    assert _fitness([whole]) < _fitness([cut])


def test_pieces_thinner_than_15_percent_of_the_narrow_side_are_waste():
    from nesting import Placement, Sheet
    from nesting_poly import free_rectangles
    thin = Sheet(1000, 500, [Placement("p", 0, 0, 1000, 430, False)])      # leaves a 70 mm strip (14 % of 500)
    ok = Sheet(1000, 500, [Placement("p", 0, 0, 1000, 400, False)])        # leaves a 100 mm strip (20 %)
    assert free_rectangles(thin, margin=0) == []
    assert len(free_rectangles(ok, margin=0)) == 1
