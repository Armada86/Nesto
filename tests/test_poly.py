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


@pytest.mark.parametrize("tri_pts", [[(240, 0), (240, 480), (0, 240)],     # drawn point-first (hypotenuse on the right)
                                      [(0, 0), (0, 480), (240, 240)]])      # drawn hypotenuse-first
def test_triangle_sits_flush_against_the_neighbour_not_point_first(tri_pts):
    """Regression (sheet 9): a right triangle (hypotenuse 480, vertical) after a row of tall plates must be
    placed with its hypotenuse flush against the last plate, not with its point facing the plate
    (both have the same length, the old engine picked one arbitrarily - the one drawn first)."""
    from shapes import geom_from_json
    plates = PolyPart("plate", box(0, 0, 200, 480), 4, rotate=False)
    tri = PolyPart("tri", Polygon(tri_pts), 1)
    s = nest_polygons([plates, tri], 1100, 500, margin=5, spacing=3, rot_step=90, time_limit=0)
    check(s, 1100, 500, 5, 3)
    assert len(s) == 1
    g = geom_from_json(next(p for p in s[0].placements if p.part == "tri").polys)
    x0, y0, x1, y1 = g.bounds
    # flush = the triangle's left edge is a full-height vertical line: a thin band along it touches the outline
    # near the top AND the bottom (point-first would only touch at the middle)
    assert g.intersects(box(x0, y0, x0 + 6, y0 + 40)) and g.intersects(box(x0, y1 - 40, x0 + 6, y1))
    last_plate = max((p for p in s[0].placements if p.part == "plate"), key=lambda p: p.x + p.w)
    assert x0 - (last_plate.x + last_plate.w) < 12                            # and it touches that plate (spacing only)


def test_compact_free_space_wins_a_tie():
    from nesting import Placement, Sheet
    from nesting_poly import _fitness
    a = Sheet(1000, 500, [Placement("p", 0, 0, 10, 10, False)]); a.free_edge = 4000.0
    b = Sheet(1000, 500, [Placement("p", 0, 0, 10, 10, False)]); b.free_edge = 6000.0
    assert _fitness([a]) < _fitness([b])


# ------------------------------------------------------------------ settle pass + bottom preference
def _sheet_of(geoms, w=1000, h=600):
    from nesting import Placement, Sheet
    from shapes import geom_to_json
    sh = Sheet(w, h)
    for name, g in geoms:
        x0, y0, x1, y1 = g.bounds
        sh.placements.append(Placement(name, x0, y0, x1 - x0, y1 - y0, False, 0.0, g.area, geom_to_json(g)))
    return sh


def _geoms(sh):
    from shapes import geom_from_json
    return [geom_from_json(p.polys) for p in sh.placements]


def _valid(sh, margin, spacing):
    gs = _geoms(sh)
    area = box(margin, margin, sh.width - margin, sh.height - margin).buffer(1e-6)
    assert all(area.contains(g) for g in gs)
    for i, a in enumerate(gs):
        for b in gs[i + 1:]:
            assert a.distance(b) >= spacing - 1e-6


def test_settle_drops_a_floating_part_flush_to_the_corner():
    from nesting_poly import settle_sheet
    sh = _sheet_of([("p", box(400, 300, 500, 400))])               # floating in the middle of an empty sheet
    assert settle_sheet(sh, margin=5, spacing=3) == 1
    p = sh.placements[0]
    assert (p.x, p.y) == pytest.approx((5, 5), abs=0.6)
    _valid(sh, 5, 3)


def test_settle_stops_at_the_spacing_and_leaves_tight_parts_alone():
    from nesting_poly import settle_sheet
    floor = box(5, 5, 995, 105)                                     # a plate on the bottom
    top = box(300, 250, 400, 350)                                   # floating above it
    sh = _sheet_of([("floor", floor), ("top", top)])
    settle_sheet(sh, margin=5, spacing=3)
    g = {p.part: gg for p, gg in zip(sh.placements, _geoms(sh))}
    assert g["floor"].bounds == pytest.approx(floor.bounds)         # already tight: not moved
    assert g["top"].bounds[1] == pytest.approx(108, abs=0.6)         # rests on the plate with the 3 mm gap
    assert g["top"].bounds[0] == pytest.approx(5, abs=0.6)           # and slides left to the margin
    _valid(sh, 5, 3)


def test_settle_respects_a_part_nested_inside_a_hole():
    from nesting_poly import settle_sheet
    ring = box(300, 300, 600, 500).difference(box(380, 380, 520, 420))      # big plate with a slot-like hole
    inner = box(430, 385, 470, 415)                                          # small part lying in the hole
    sh = _sheet_of([("ring", ring), ("inner", inner)])
    settle_sheet(sh, margin=5, spacing=3)
    _valid(sh, 5, 3)                                                         # sweeping the ring never hits the inner part


def test_settle_keeps_the_original_sheet_if_scrap_would_get_worse(monkeypatch):
    import nesting_poly as npl
    sh = _sheet_of([("p", box(400, 300, 500, 400))])
    calls = []
    def fake_key(sheet, margin):                     # pretend any moved sheet has worse scrap
        calls.append(1)
        return (0,) if len(calls) % 2 == 0 else (1,)   # 1st call = the settled trial (worse), 2nd = the original
    monkeypatch.setattr(npl, "_sheet_key", fake_key)
    sheets = [sh]
    npl.settle_layout(sheets, 5, 3)
    assert sheets[0] is sh and sheets[0].placements[0].x == pytest.approx(400)


def test_nest_polygons_result_stays_valid_after_settling():
    parts = [PolyPart("c", build_shape("circle", 120), 6), PolyPart("t", build_shape("triangle", 200, 150), 6),
             PolyPart("r", build_shape("rectangle", 300, 100), 4)]
    info = {}
    s = nest_polygons(parts, 900, 600, 5, 3, 90, time_limit=0, info=info)
    check(s, 900, 600, 5, 3)
    assert sum(len(x.placements) for x in s) == 16 and "settled_parts" in info


def test_lower_layout_wins_a_tie():
    from nesting import Placement, Sheet
    from nesting_poly import _fitness
    low = Sheet(1000, 500, [Placement("p", 0, 10, 100, 100, False)]); high = Sheet(1000, 500, [Placement("p", 0, 380, 100, 100, False)])
    assert _fitness([low]) < _fitness([high])


def _grid_with(parts, w=1000, h=500, margin=5, spacing=3, cell=1.0):
    """A _Grid with the given (polygon, x, y) parts committed at fixed positions."""
    import nesting_poly as npl
    g = npl._Grid(w, h, margin, spacing, cell)
    pad = spacing / 2 + npl.PAD_FACTOR * cell
    for geom, x, y in parts:
        from shapes import normalize
        v = npl._variants(PolyPart("p", normalize(geom), 1, rotate=False), 90, cell, pad)[0]
        g.commit(v.mask, int(round((x - pad) / cell)), int(round((y - pad) / cell)))
    return g


def test_small_part_takes_the_smallest_gap_not_the_big_open_region():
    """Rule: small parts go to the smallest scrap area (tight pocket), not into a big/long scrap region."""
    import nesting_poly as npl
    cell, pad = 1.0, 1.5 + 0.75
    plate_sheet = _grid_with([(box(0, 0, 980, 380), 10, 10)])                       # sheet 1: leaves a regular strip on top
    frame = box(0, 0, 300, 300).difference(box(100, 100, 200, 200))                 # sheet 2: a frame with a 100 x 100 hole ...
    wedge = Polygon([(0, 0), (600, 0), (0, 300)])                                  # ... and a triangle (big irregular open region)
    pocket_sheet = _grid_with([(frame, 10, 10), (wedge, 330, 10)])
    small = PolyPart("s", box(0, 0, 30, 30), 1, rotate=False)
    variants = npl._variants(small, 90, cell, pad)
    min_cells = min(int(v.mask.sum()) for v in variants)
    g, cand = npl._small_part_spot([plate_sheet, pocket_sheet], variants, min_cells, "xend", None, 0.0)
    assert g is pocket_sheet
    k, ox, oy, dmg = cand
    assert dmg == 0
    # ... and inside the 100 x 100 hole of the frame (x 110..210, y 110..210), the smallest region
    assert 100 <= ox and ox + variants[k].mask.shape[1] <= 220 and 100 <= oy and oy + variants[k].mask.shape[0] <= 220


def test_gap_cache_is_dropped_when_a_sheet_changes():
    import nesting_poly as npl
    g = _grid_with([(box(0, 0, 400, 300), 10, 10)])
    small = PolyPart("s", box(0, 0, 30, 30), 1, rotate=False)
    variants = npl._variants(small, 90, 1.0, 2.25)
    mc = min(int(v.mask.sum()) for v in variants)
    first = npl._small_part_spot([g], variants, mc, "xend", None, 0.0)
    assert g.gap_cache                                            # cached for the next copy of the same part
    k, ox, oy, _ = first[1]
    g.commit(variants[k].mask, ox, oy)
    assert not g.gap_cache                                        # sheet changed: stale candidates are gone
    second = npl._small_part_spot([g], variants, mc, "xend", None, 0.0)
    assert second[1][1:3] != (ox, oy)                             # the next copy goes to a different place


def test_layout_with_scrap_beats_layout_without_and_more_pieces_are_not_worse():
    """Regression: tuples of different length compared by prefix, so () beat (-0.2, ...) - 'no scrap' won."""
    from nesting import Placement, Sheet
    from nesting_poly import _fitness
    def sheet(scrap):
        s = Sheet(1000, 500, [Placement("p", 0, 0, 10, 10, False)]); s.scrap = scrap; return s
    none = [sheet([])]
    one = [sheet([(0, 0, 400, 400)])]
    two = [sheet([(0, 0, 400, 400), (500, 0, 300, 300)])]
    assert _fitness(one) < _fitness(none)          # having scrap is better than having none
    assert _fitness(two) < _fitness(one)           # an extra piece never makes a layout worse


# ------------------------------------------------------------------------------------------------ flipping
def _chiral(seed=3, size=100.0):
    """A random star-shaped polygon: no mirror symmetry (its mirror image is not a rotation of it)."""
    import math, random
    from shapes import normalize
    rng = random.Random(seed)
    n = 8
    angs = sorted(rng.uniform(0, 2 * math.pi) for _ in range(n))
    g = Polygon([(size * rng.uniform(0.35, 1.0) * math.cos(a), size * rng.uniform(0.35, 1.0) * math.sin(a)) for a in angs])
    return normalize(g if g.is_valid else g.buffer(0))


def test_chiral_helper_really_is_chiral():
    from shapely import affinity
    g = _chiral()
    m = affinity.scale(g, -1, 1, origin=(0, 0))
    # simpler and exact enough: after centring both on their centroid, no rotation makes the mirror image match
    gc = affinity.translate(g, -g.centroid.x, -g.centroid.y)
    mc = affinity.translate(m, -m.centroid.x, -m.centroid.y)
    diffs = [affinity.rotate(mc, a, origin=(0, 0)).symmetric_difference(gc).area / g.area for a in range(0, 360, 2)]
    assert min(diffs) > 0.15


def test_flip_adds_mirrored_variants_only_for_shapes_without_mirror_symmetry():
    import nesting_poly as npl
    def count(geom, flip):
        return len(npl._variants(PolyPart("p", geom, 1, True, flip=flip), 90, 1.0, 2.0))
    chiral = _chiral()
    assert count(chiral, True) > count(chiral, False)                     # mirror images are new orientations
    assert all(not v.mirror for v in npl._variants(PolyPart("p", chiral, 1, True, flip=False), 90, 1.0, 2.0))
    assert any(v.mirror for v in npl._variants(PolyPart("p", chiral, 1, True, flip=True), 90, 1.0, 2.0))
    for symmetric in (box(0, 0, 80, 30), build_shape("isosceles", 1, 1) if False else Polygon([(0, 0), (60, 0), (30, 50)])):
        assert count(symmetric, True) == count(symmetric, False)          # symmetric: flipping costs nothing


def test_flipping_lets_a_part_into_a_pocket_shaped_like_its_mirror_image():
    """A frame has a hole that is (slightly bigger than) the MIRROR image of the part. Turning the part over fits
    it in the hole; without flipping it cannot enter and needs a second sheet."""
    from shapely import affinity
    part = _chiral()
    mirror = affinity.scale(part, -1, 1, origin=(0, 0))
    hole = mirror.buffer(3.5)                                                       # 3.5 mm clearance all round
    hole = affinity.translate(hole, 200 - hole.centroid.x, 200 - hole.centroid.y)
    frame = box(0, 0, 400, 400).difference(hole)
    def run(flip):
        parts = [PolyPart("frame", frame, 1, rotate=False, flip=False), PolyPart("part", part, 1, rotate=True, flip=flip)]
        return nest_polygons(parts, 410, 410, margin=3, spacing=2, rot_step=15, time_limit=0)
    with_flip, without = run(True), run(False)
    check(with_flip, 410, 410, 3, 2)
    assert len(with_flip) == 1 and len(without) == 2
    assert any(p.mirrored for p in with_flip[0].placements if p.part == "part")


def test_flip_off_never_mirrors():
    parts = [PolyPart(f"p{i}", _chiral(i), 1, flip=False) for i in range(6)]
    s = nest_polygons(parts, 700, 500, 5, 3, 30, time_limit=0)
    check(s, 700, 500, 5, 3)
    assert not any(p.mirrored for sh in s for p in sh.placements)


def test_pairs_of_flipped_parts_stay_valid():
    """Clusters (rigid pairs / quads) may contain a mirrored copy; unpacking them must give correct, disjoint parts."""
    part = _chiral(5, 120)
    s = nest_polygons([PolyPart("c", part, 8, flip=True)], 900, 600, 5, 3, 45, time_limit=0)
    check(s, 900, 600, 5, 3)
    assert sum(len(x.placements) for x in s) == 8
    for sh in s:
        for p in sh.placements:
            assert p.area == pytest.approx(part.area, rel=1e-3)           # every placed part has the part's own area


def test_fine_rotation_step_is_never_worse_and_reports_the_fine_pass():
    from shapely.geometry import Polygon
    import nesting_poly as npl
    parts = [npl.PolyPart("t", Polygon([(0, 0), (260, 0), (90, 170)]), 6), npl.PolyPart("b", Polygon([(0, 0), (300, 0), (300, 60), (0, 60)]), 3)]
    coarse, fine = {}, {}
    a = npl.nest_polygons(parts, 1000, 600, 5, 3, rot_step=15, cell=8, info=coarse)
    b = npl.nest_polygons(parts, 1000, 600, 5, 3, rot_step=2, cell=8, info=fine)
    assert fine["fine_step"] == 2.0 and coarse.get("fine_step", 0.0) == 0.0
    assert npl._fitness(b) <= npl._fitness(a)                                  # the pass only replaces a layout when better
    assert sum(len(s.placements) for s in b) == 9


def test_extra_orientations_are_added_to_the_variants():
    from shapely.geometry import Polygon
    import nesting_poly as npl
    p = npl.PolyPart("t", Polygon([(0, 0), (260, 0), (90, 170)]), 1, flip=False)
    q = npl.PolyPart("t", p.geom, 1, flip=False, extra=((7.0, False), (352.0, False)))
    base = {round(v.ang, 1) for v in npl._variants(p, 15.0, 8.0, 2.0, False)}
    more = {round(v.ang, 1) for v in npl._variants(q, 15.0, 8.0, 2.0, False)}
    assert {7.0, 352.0} <= more and not ({7.0, 352.0} & base) and base <= more


def test_scrap_pieces_reach_the_sheet_edge_over_the_margin_but_not_through_parts():
    import nesting_poly as npl
    from nesting import Placement, Sheet
    from shapely.geometry import box
    from shapes import geom_to_json
    g = box(5, 5, 305, 495)                                                    # a part against the left margin
    sh = Sheet(1000, 500, [Placement("a", 5, 5, 300, 490, False, 0.0, g.area, geom_to_json(g), False)])
    plain = npl.free_rectangles(sh, 5)
    shown = npl.scrap_rectangles(sh, 5)
    x, y, w, h = shown[0]
    assert abs(x + w - 1000) < 1e-6 and abs(y) < 1e-6 and abs(y + h - 500) < 1e-6      # reaches the right, bottom and top edge
    assert x > 305                                                                    # the part side is NOT extended
    px, py, pw, ph = plain[0]
    assert w > pw and h > ph and abs(x - px) < 1e-6                                   # the margin strips were added
    assert npl.scrap_rectangles(sh, 0) == npl.free_rectangles(sh, 0)                  # no margin, nothing to add
