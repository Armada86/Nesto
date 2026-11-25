"""CAM: toolpaths are offset by the tool radius, ordered, safe, and the G-code follows the post's blocks."""
import math
import pathlib
import re
import sys
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
import pytest
from shapely.geometry import LineString, Point, box

import cam
import machine_profile as machine
import post_fanuc as pf
from layout_edit import placement_geom
from shapes import geom_to_json


def pl(name, g):
    b = g.bounds
    return {"part": name, "x": b[0], "y": b[1], "w": b[2] - b[0], "h": b[3] - b[1], "polys": geom_to_json(g)}


@pytest.fixture()
def sheet():
    return {"width": 1000.0, "height": 600.0, "placements": [
        pl("A", box(10, 10, 300, 200).difference(box(100, 60, 200, 150))),      # a plate with a hole
        pl("B", box(310, 10, 600, 200)),
        pl("C", Point(750, 300).buffer(60, 32))]}                                # a disc


TOOL = cam.Tool(1, "EM 6MM", 6.0, 2, 12000, 4000, 1500, 6.0, "flood")


def run(sheet, job=None, tool=TOOL, spacing=3.0, margin=5.0):
    job = job or cam.CamJob(thickness=18.0)
    plan = cam.plan_sheet(sheet, job, {1: tool}, margin, spacing)
    grp = cam.groups(plan, job, {1: tool})
    text = pf.program("T.anc", grp, job, 1, (1000, 600), "P", 7)
    return plan, grp, text, job


def parse(text):
    """The G-code interpreter used for the checks: [(code, x, y, z, f)] of every G0 / G1 block (modal axes and feed)."""
    x = y = z = f = None
    out = []
    for line in text.splitlines():
        line = re.sub(r"\(.*?\)", "", line).strip()
        if not line or line[0] in "%<":
            continue
        toks = line.split()
        if toks[0] in ("G0", "G1") and not any(t.startswith("G28") for t in toks):
            if any(t.startswith("G43") for t in toks):
                toks = [t for t in toks if not t.startswith(("G43", "H"))]
            for t in toks[1:]:
                v = float(t[1:])
                if t[0] == "X": x = v
                elif t[0] == "Y": y = v
                elif t[0] == "Z": z = v
                elif t[0] == "F": f = v
            out.append((toks[0], x, y, z, f))
    return out


def test_every_cutting_point_keeps_a_tool_radius_from_all_parts_and_the_path_is_closed(sheet):
    plan, grp, text, job = run(sheet)
    geoms = [placement_geom(p) for p in sheet["placements"]]
    r = TOOL.diameter / 2
    final = -job.depth()
    for code, x, y, z, f in parse(text):
        if code == "G1":
            for g in geoms:
                assert not g.contains(Point(x, y)), (x, y)                      # never on a part
                assert g.distance(Point(x, y)) >= r - 0.02, (x, y)              # the tool edge never reaches a part
    assert len(plan.contours) == 4                                            # hole + three outlines
    for c in plan.contours:
        assert c.pts[0] == c.pts[-1]                                           # closed loops
    assert min(z for _, _, _, z, _ in parse(text) if z is not None) == pytest.approx(final, abs=1e-6)


def test_cutting_order_inner_contours_first_and_climb_direction(sheet):
    plan, *_ = run(sheet)
    kinds = [c.kind for c in plan.contours]
    assert kinds == sorted(kinds, key=lambda k: k != "inner")                  # all inner contours before any outline
    for c in plan.contours:
        area = cam._signed_area(c.pts)
        assert (area > 0) == (c.kind == "inner")                                # climb: holes counter-clockwise, outlines clockwise
    conv, *_ = run(sheet, cam.CamJob(thickness=18.0, climb=False))
    for c in conv.contours:
        assert (cam._signed_area(c.pts) > 0) == (c.kind == "outer")


def test_outline_distance_is_the_tool_radius_and_holes_are_cut_from_inside(sheet):
    plan, *_ = run(sheet)
    r = 3.0
    hole = next(c for c in plan.contours if c.kind == "inner")
    for x, y in hole.pts:
        assert 100 + r - 0.02 <= x <= 200 - r + 0.02 and 60 + r - 0.02 <= y <= 150 - r + 0.02    # inside the 100 x 90 hole, one radius from the wall
    outer_a = next(c for c in plan.contours if c.kind == "outer" and c.part == "A")
    line = LineString(outer_a.pts)
    assert line.distance(box(10, 10, 300, 200)) == pytest.approx(r, abs=0.02) or all(
        abs(box(10, 10, 300, 200).distance(Point(p)) - r) < 0.02 for p in outer_a.pts)


def test_depth_passes_ramp_and_straight_plunge(sheet):
    job = cam.CamJob(thickness=18.0, overcut=0.3, ramp=False)
    plan, grp, text, job = run(sheet, job)
    zs = sorted({round(z, 3) for _, _, _, z, _ in parse(text) if z is not None and z < 0})
    assert zs[0] == pytest.approx(-18.3) and len(zs) == math.ceil(18.3 / 6.0)  # 4 equal passes of 4.575 mm, none deeper than 6 mm
    assert all(b - a <= 6.0 + 1e-6 for a, b in zip([0.0] + zs[::-1], zs[::-1]))
    ramp = run(sheet, cam.CamJob(thickness=18.0, ramp=True))[2]
    assert any(m[3] is not None and -18.3 < m[3] < 0 and m[0] == "G1" for m in parse(ramp))   # descends along the contour


def test_tabs_lift_the_tool_on_the_last_pass_of_the_outlines_only(sheet):
    job = cam.CamJob(thickness=18.0, tabs=3, tab_width=10.0, tab_height=2.0)
    plan, grp, text, job = run(sheet, job)
    z_tab = -18.0 + 2.0
    moves = parse(text)
    lifted = [m for m in moves if m[3] is not None and abs(m[3] - z_tab) < 1e-6]
    assert lifted                                                              # tabs exist
    # three tabs on each of the three outlines: 9 transitions up to the tab height, none on the hole
    ups = sum(1 for a, b in zip(moves, moves[1:]) if a[3] is not None and b[3] is not None and a[3] < z_tab - 1e-6 and abs(b[3] - z_tab) < 1e-6)
    assert ups == 9


def test_warnings_for_a_tool_too_big_for_the_gap_a_hole_too_small_and_a_missing_tool(sheet):
    big = cam.Tool(1, "BIG", 8.0, 2, 12000, 4000, 1500, 6.0)
    plan = cam.plan_sheet(sheet, cam.CamJob(thickness=18.0), {1: big}, margin=5.0, spacing=3.0)
    sheet2 = {"width": 1000.0, "height": 600.0, "placements": [
        pl("A", box(10, 10, 300, 200).difference(box(100, 60, 105, 65))),        # a 5 mm hole
        pl("B", box(304, 10, 600, 200))]}                                         # 4 mm gap
    plan2 = cam.plan_sheet(sheet2, cam.CamJob(thickness=18.0), {1: TOOL}, margin=5.0, spacing=4.0)
    assert any("smaller than the tool" in w for w in plan2.warnings) and plan2.skipped == 1
    assert any("neighbouring part closer than the tool diameter" in w for w in plan2.warnings)
    none = cam.plan_sheet(sheet, cam.CamJob(thickness=18.0, outer_tool=9), {1: TOOL}, 5.0, 3.0)
    assert none.contours == [] and any("T9" in w for w in none.warnings)
    assert not cam.plan_sheet(sheet, cam.CamJob(thickness=18.0), {1: TOOL}, 5.0, 8.0).warnings   # all fine


def test_program_follows_the_posts_blocks(sheet):
    plan, grp, text, job = run(sheet)
    lines = text.splitlines()
    assert lines[0] == "%" and lines[1] == "<T.anc>" and lines[-2] == "M30" and lines[-1] == "%"
    for must in ("G90 G21 G40 G49 G80 G94 G98 G17", "G0 G28 G91 Z0.", "G56", "M6 T1", "G0 C0.", "S12000 M3", "G94",
                 "G05.1 Q1 R7 (AICC/AIAPC HIGH SPEED MODE ON)", "M8", "G0 G43 H1 Z5.", "G05.1 Q0 (AICC/AIAPC HIGH SPEED MODE OFF)", "M5"):
        assert must in lines, must
    order = [lines.index(x) for x in ("G56", "M6 T1", "S12000 M3", "M8", "G0 G43 H1 Z5.")]
    assert order == sorted(order)                                              # the same order as in the post
    assert text.count("(") == text.count(")") and not re.search(r"^N\d", text, re.M)   # comments balanced, no block numbers
    assert all(ord(ch) < 128 for ch in text)


def test_two_tools_make_a_tool_change_and_the_machine_limits_match_the_machine_file(sheet):
    t2 = cam.Tool(2, "FINISH 3MM", 3.0, 2, 14000, 3000, 1000, 3.0, "none")
    job = cam.CamJob(thickness=18.0, inner_tool=2, outer_tool=1)
    plan = cam.plan_sheet(sheet, job, {1: TOOL, 2: t2}, 5.0, 3.0)
    grp = cam.groups(plan, job, {1: TOOL, 2: t2})
    text = pf.program("T.anc", grp, job, 1, (1000, 600))
    assert [g[0].number for g in grp] == [2, 1] and "M6 T2" in text and "M6 T1" in text and text.count("M1\n") == 1
    assert text.index("M6 T2") < text.index("M6 T1")
    m = machine.read_machine_file()
    assert m["max_rpm"] == machine.MAX_RPM and m["tool_positions"] == machine.MAX_TOOL_NUMBER
    assert m["rapid_X"] == machine.RAPID_XY and m["rapid_Z"] == machine.RAPID_Z
    assert cam.Tool(27, "x").problems() and cam.Tool(1, "x", rpm=20000).problems() and not TOOL.problems()


def test_number_format():
    assert [pf.num(v) for v in (12.0, -3.5, 0.0, -0.0004, 1.23456, 100.0)] == ["12.", "-3.5", "0.", "0.", "1.235", "100."]
