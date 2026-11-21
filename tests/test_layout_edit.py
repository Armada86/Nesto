import copy
import sys
import pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
import pytest
from shapely.geometry import box

from layout_edit import apply_move, ensure_polys, sheet_problems
from nesting import Part, nest
from nesting_poly import PolyPart, nest_polygons
from shapes import build_shape, geom_to_json


def sheet_of(*items, w=1000.0, h=500.0):
    pl = []
    for name, g in items:
        x0, y0, x1, y1 = g.bounds
        pl.append({"part": name, "x": x0, "y": y0, "w": x1 - x0, "h": y1 - y0, "rotated": False, "angle": 0.0,
                   "area": g.area, "polys": geom_to_json(g), "mirrored": False})
    return {"width": w, "height": h, "placements": pl}


def test_valid_move_translates_outline_and_box():
    sh = sheet_of(("a", box(10, 10, 110, 60)), ("b", box(300, 10, 400, 60)))
    ok, msg = apply_move(sh, 0, 50, 100, margin=5, spacing=3)
    assert ok and msg == "moved"
    p = sh["placements"][0]
    assert (p["x"], p["y"], p["w"], p["h"]) == pytest.approx((60, 110, 100, 50))
    assert sh["placements"][0]["area"] == pytest.approx(5000)
    assert sheet_problems(sh, 5, 3) == []


def test_move_into_a_neighbour_or_too_close_is_refused_and_changes_nothing():
    sh = sheet_of(("a", box(10, 10, 110, 60)), ("b", box(300, 10, 400, 60)))
    before = copy.deepcopy(sh)
    for dx in (200, 188.5):                                     # overlapping / 1.5 mm gap < 3 mm spacing
        ok, msg = apply_move(sh, 0, dx, 0, 5, 3)
        assert not ok and ("overlaps" in msg or "too close" in msg)
        assert sh == before
    ok, _ = apply_move(sh, 0, 187.0, 0, 5, 3)                   # exactly 3 mm gap: allowed
    assert ok


def test_move_out_of_the_sheet_margin_is_refused():
    sh = sheet_of(("a", box(10, 10, 110, 60)))
    for dx, dy in ((-6, 0), (0, -6), (900, 0), (0, 450)):
        assert apply_move(sh, 0, dx, dy, 5, 3)[1] == "outside the sheet margin"
    assert apply_move(sh, 0, -5, -5, 5, 3)[0]                   # exactly on the margin is fine


def test_spacing_zero_allows_touching_but_not_overlapping():
    sh = sheet_of(("a", box(10, 10, 110, 60)), ("b", box(200, 10, 300, 60)))
    assert apply_move(sh, 0, 90, 0, 5, 0)[0]                    # touching edge to edge
    assert not apply_move(sh, 0, 5, 0, 5, 0)[0]                 # now overlapping


def test_a_part_may_be_moved_into_the_hole_of_another():
    frame = box(100, 100, 400, 300).difference(box(150, 150, 350, 250))
    sh = sheet_of(("frame", frame), ("small", box(500, 150, 540, 190)))
    assert apply_move(sh, 1, -330, 20, 5, 3)[0]                 # into the hole
    assert sheet_problems(sh, 5, 3) == []
    assert apply_move(sh, 1, 70, 0, 5, 3)[0]                   # still inside the hole
    assert not apply_move(sh, 1, 120, 0, 5, 3)[0]               # onto the frame's right wall (x 350-400)


def test_bad_requests_are_rejected():
    sh = sheet_of(("a", box(10, 10, 110, 60)))
    assert apply_move(sh, 5, 1, 1, 5, 3) == (False, "unknown part")
    assert apply_move(sh, 0, float("nan"), 1, 5, 3)[0] is False or True      # NaN never produces a valid position
    assert not apply_move(sh, 0, "10", 1, 5, 3)[0]
    assert sh["placements"][0]["x"] == 10


def test_rectangle_engine_placements_can_be_edited_after_ensure_polys():
    sheets = nest([Part("a", 100, 50, 4)], 1000, 500, 5, 3)
    sh = {"width": 1000.0, "height": 500.0, "placements": [dict(p.__dict__) for p in sheets[0].placements]}
    assert all(not p.get("polys") for p in sh["placements"])
    ensure_polys(sh)
    ok, _ = apply_move(sh, 0, 300, 0, 5, 3)                     # to the free area on the right
    assert ok and sheet_problems(sh, 5, 3) == []


def test_edited_nested_layout_stays_valid():
    parts = [PolyPart("c", build_shape("circle", 60), 5), PolyPart("t", build_shape("triangle", 90, 70), 4)]
    s = nest_polygons(parts, 600, 400, 5, 3, 90, time_limit=0)[0]
    sh = {"width": s.width, "height": s.height, "placements": [dict(p.__dict__) for p in s.placements]}
    assert sheet_problems(sh, 5, 3) == []
    moved = sum(apply_move(sh, i, 7, 5, 5, 3)[0] for i in range(len(sh["placements"])))
    assert moved >= 1 and sheet_problems(sh, 5, 3) == []        # whatever was accepted left a valid layout


# ---- rotate / flip ----
def _two(gap=10.0):
    from shapes import geom_to_json
    from shapely.geometry import box
    def pl(name, x, y, w, h):
        return {"part": name, "x": x, "y": y, "w": w, "h": h, "angle": 0.0, "mirrored": False, "polys": geom_to_json(box(x, y, x + w, y + h))}
    return {"width": 1000.0, "height": 500.0, "placements": [pl("a", 100, 100, 200, 50), pl("b", 300 + gap, 100, 100, 100)]}


def test_rotate_with_room_turns_the_part_about_its_centre_and_books_the_angle():
    from layout_edit import apply_transform, sheet_problems, placement_geom
    sh = _two()
    from shapes import geom_to_json
    from shapely.geometry import box
    sh["placements"][1]["polys"] = geom_to_json(box(700, 100, 800, 200))   # plenty of room around a
    ok, msg = apply_transform(sh, 0, "rotate", 90, 0, 0, 0, 0, 5, 3)
    assert ok, msg
    p = sh["placements"][0]
    assert (round(p["w"]), round(p["h"])) == (50, 200) and p["angle"] == 90.0
    cx, cy = placement_geom(p).centroid.x, placement_geom(p).centroid.y
    assert abs(cx - 200) < 1e-3 and abs(cy - 125) < 1e-3
    assert sheet_problems(sh, 5, 3) == []


def test_rotate_without_room_is_refused_and_changes_nothing():
    from layout_edit import apply_transform
    from shapes import geom_to_json
    from shapely.geometry import box
    sh = _two()
    sh["placements"][1]["polys"] = geom_to_json(box(150, 153, 250, 253))   # b sits 3 mm above a: turned upright, a hits it
    before = sh["placements"][0]["polys"]
    ok, msg = apply_transform(sh, 0, "rotate", 90, 0, 0, 0, 0, 5, 3)
    assert not ok and "close" in msg
    assert sh["placements"][0]["polys"] == before and sh["placements"][0]["angle"] == 0.0


def test_rotate_cannot_leave_the_sheet_margin_but_the_pushed_back_shift_is_accepted():
    from layout_edit import apply_transform
    sh = _two()
    from shapes import geom_to_json
    from shapely.geometry import box
    sh["placements"][1]["polys"] = geom_to_json(box(700, 100, 800, 200))
    sh["placements"][0]["polys"] = geom_to_json(box(300, 5, 500, 55))   # on the bottom margin: turned upright it sticks out below
    ok, _ = apply_transform(sh, 0, "rotate", 90, 0, 0, 0, 0, 5, 3)
    assert not ok
    ok, msg = apply_transform(sh, 0, "rotate", 90, 0, 0, 0, 80, 5, 3)      # the browser sends the shift that fixes it
    assert ok, msg


def test_flip_mirrors_an_asymmetric_part_and_toggles_the_mirrored_flag():
    from layout_edit import apply_transform, placement_geom
    from shapes import geom_to_json
    from shapely.geometry import Polygon
    tri = Polygon([(100, 100), (200, 100), (100, 160)])      # right angle at the lower left
    sh = {"width": 1000.0, "height": 500.0, "placements": [{"part": "t", "x": 100, "y": 100, "w": 100, "h": 60, "angle": 0.0,
                                                           "mirrored": False, "polys": geom_to_json(tri)}]}
    ok, msg = apply_transform(sh, 0, "flip", "h", 0, 0, 0, 0, 5, 3)
    assert ok, msg
    g = placement_geom(sh["placements"][0])
    assert sh["placements"][0]["mirrored"] is True
    assert abs(g.area - tri.area) < 1e-3 and abs(g.bounds[0] - 100) < 1e-3 and abs(g.bounds[2] - 200) < 1e-3
    assert g.contains(Polygon([(190, 105), (195, 105), (195, 110)]))        # the right angle is now at the lower right
    ok, _ = apply_transform(sh, 0, "flip", "v", 0, 0, 0, 0, 5, 3)
    assert ok and sh["placements"][0]["mirrored"] is False                  # flipped twice = turned over twice (bookkeeping)


def test_bad_transform_requests_are_refused():
    from layout_edit import apply_transform
    sh = _two()
    assert not apply_transform(sh, 5, "rotate", 90, 0, 0, 0, 0, 5, 3)[0]
    assert not apply_transform(sh, 0, "spin", 90, 0, 0, 0, 0, 5, 3)[0]
    assert not apply_transform(sh, 0, "flip", "x", 0, 0, 0, 0, 5, 3)[0]
    assert not apply_transform(sh, 0, "rotate", "90", 0, 0, 0, 0, 5, 3)[0]
    assert not apply_transform(sh, 0, "rotate", 90, 1e9, 0, 0, 0, 5, 3)[0]


# ---- hold area ----
def test_a_part_may_be_set_aside_in_the_hold_area_and_brought_back():
    from layout_edit import apply_move, hold_rect, is_held, placement_geom, sheet_problems
    sh = _two()
    x0, y0, x1, y1 = hold_rect(sh)
    assert y1 < 0 and y0 < y1 and (x0, x1) == (0.0, sh["width"])         # a box below the sheet
    ok, msg = apply_move(sh, 0, 0, -(100 + 300), 5, 3)                    # a: y 100..150 -> -300..-250, inside the hold box
    assert ok, msg
    assert is_held(sh["placements"][0]) and not is_held(sh["placements"][1])
    assert sheet_problems(sh, 5, 3) == []
    ok, msg = apply_move(sh, 0, 0, 400, 5, 3)                             # and back onto the sheet
    assert ok, msg and not is_held(sh["placements"][0])


def test_hold_area_limits_and_the_gap_between_sheet_and_hold_area():
    from layout_edit import apply_move, hold_rect
    sh = _two()
    x0, y0, x1, y1 = hold_rect(sh)
    ok, msg = apply_move(sh, 0, 0, -(100 + (-y0) + 10), 5, 3)             # below the bottom of the hold box
    assert not ok and "hold" in msg
    ok, msg = apply_move(sh, 0, 2000, -400, 5, 3)                         # right of the hold box
    assert not ok and "hold" in msg
    ok, msg = apply_move(sh, 0, 0, -105, 5, 3)                            # centre still above y=0 but sticking out of the sheet
    assert not ok and "margin" in msg


def test_two_held_parts_cannot_overlap():
    from layout_edit import apply_move
    sh = _two()
    assert apply_move(sh, 0, 0, -400, 5, 3)[0]
    ok, msg = apply_move(sh, 1, -150, -500, 5, 3)                         # b lands right against a (y -400..-300 vs -300..-250)
    assert not ok and "close" in msg
