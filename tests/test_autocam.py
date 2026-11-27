"""AutoCam: the choice of the tools and the operations, the drilling cycles and the program of a whole sheet."""
import math
import pathlib
import re
import sys
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
from shapely.geometry import Point, box

import autocam
import cam
import gcode_cam
import post_fanuc
from shapes import geom_to_json

BASE = {"flutes": 3, "shoulder_diameter": 6.0, "arbor_diameter": 6.0, "shoulder_angle": 0.0, "cutting_length": 40.0, "shoulder_length": 50.0,
        "total_length": 50.0, "chamfer": 0.0, "outside_holder": 100.0, "helical_angle": 30.0, "holder": "", "point_angle": None}


def tool(no, kind, d, name=None):
    return {**BASE, "number": no, "tool_type": kind, "name": name or f"{kind} {d:g}", "diameter": float(d)}


def pl(name, g):
    b = g.bounds
    return {"part": name, "x": b[0], "y": b[1], "w": b[2] - b[0], "h": b[3] - b[1], "polys": geom_to_json(g)}


def sheet():
    """Part A 300 x 200 at (50, 50) with a round hole D20, a round hole D8 and a square hole 40 x 40; part B 100 x 100 with a D4 hole."""
    a = (box(50, 50, 350, 250).difference(Point(120, 150).buffer(10, 64)).difference(Point(200, 150).buffer(4, 64))
         .difference(box(250, 130, 290, 170)))
    b = box(420, 50, 520, 150).difference(Point(470, 100).buffer(2, 64))
    return {"width": 1000.0, "height": 600.0, "placements": [pl("A", a), pl("B", b)]}


TOOLS = [tool(1, "END MILL", 12.7), tool(2, "END MILL", 6.35), tool(3, "END MILL", 3.0), tool(4, "DRILL", 20.0), tool(5, "DRILL", 8.0)]


def test_round_holes_that_match_a_drill_are_drilled_the_others_milled_with_the_largest_end_mill_that_fits():
    p = autocam.plan(sheet(), 1, TOOLS, None, 18.0, margin=10.0, spacing=14.0)
    ops = {o["name"]: o for o in p["operations"]}
    assert [o["name"] for o in p["operations"]] == ["Sheet1-DRILL-DRILL 20", "Sheet1-DRILL-DRILL 8", "Sheet1-HOLES-END MILL 12.7", "Sheet1-HOLES-END MILL 3", "Sheet1-CONTOUR-END MILL 12.7"]
    assert [o["kind"] for o in p["operations"]] == ["drill", "drill", "hole", "hole", "outline"]       # drills, milled holes, outlines
    assert ops["Sheet1-DRILL-DRILL 20"]["tool"]["number"] == 4 and len(ops["Sheet1-DRILL-DRILL 20"]["contours"]) == 1
    assert ops["Sheet1-DRILL-DRILL 8"]["tool"]["number"] == 5
    assert ops["Sheet1-HOLES-END MILL 12.7"]["tool"]["number"] == 1                      # the 40 x 40 square hole: the largest end mill fits
    assert ops["Sheet1-HOLES-END MILL 3"]["tool"]["number"] == 3                         # the D4 hole of part B: only the 3 mm end mill fits (not D4 itself)
    assert ops["Sheet1-CONTOUR-END MILL 12.7"]["tool"]["number"] == 1 and len(ops["Sheet1-CONTOUR-END MILL 12.7"]["contours"]) == 2   # both outlines
    assert not p["problems"] and not p["skipped"]


def close_sheet(gap):
    """Two parts 100 x 100 only `gap` mm apart (and 20 mm from the edges)."""
    return {"width": 400.0, "height": 200.0, "placements": [pl("A", box(20, 20, 120, 120)), pl("B", box(120 + gap, 20, 220 + gap, 120))]}


def test_the_outline_tool_keeps_clear_of_the_neighbours_and_the_edge_of_the_sheet():
    p = autocam.plan(sheet(), 1, TOOLS, None, 18.0, margin=3.0, spacing=14.0)        # the margin 3 mm: only a tool of at most 6 mm leaves the sheet alone
    outline = [o for o in p["operations"] if o["kind"] == "outline"][0]
    assert outline["tool"]["number"] == 3 and outline["name"] == "Sheet1-CONTOUR-END MILL 3"
    p = autocam.plan(close_sheet(8.0), 1, TOOLS, None, 18.0, margin=20.0, spacing=8.0)   # the parts only 8 mm apart: at most an 8 mm tool
    outline = [o for o in p["operations"] if o["kind"] == "outline"][0]
    assert outline["tool"]["diameter"] <= 8.0 and not p["warnings"]
    p = autocam.plan(close_sheet(2.0), 1, [tool(1, "END MILL", 12.7)], None, 18.0, margin=20.0, spacing=2.0)   # nothing fits: the only tool and a warning
    assert [o for o in p["operations"] if o["kind"] == "outline"][0]["tool"]["number"] == 1 and p["warnings"]


def test_holes_too_small_for_every_tool_are_reported_and_not_cut_and_without_drills_everything_is_milled():
    p = autocam.plan(sheet(), 1, [tool(1, "END MILL", 12.7), tool(3, "END MILL", 6.0)], None, 18.0, margin=10.0, spacing=14.0)
    assert len(p["skipped"]) == 1 and "too small" in p["skipped"][0] and "B:" in p["skipped"][0]      # the D4 hole of B
    assert [o["kind"] for o in p["operations"]] == ["hole", "hole", "outline"]                          # no drill: D20 and D8 are milled too
    assert [o["tool"]["number"] for o in p["operations"] if o["kind"] == "hole"] == [1, 3]


def test_problems_without_an_end_mill_or_without_anything_to_cut():
    assert autocam.plan(sheet(), 1, [tool(4, "DRILL", 20.0)], None, 18.0)["problems"]
    assert autocam.plan({"width": 10.0, "height": 10.0, "placements": []}, 1, TOOLS, None, 18.0)["problems"]


def test_the_operations_carry_the_cutting_data_the_levels_and_the_thickness():
    saved = {"mill": {"feed_xy": 1800.0, "rpm": 12000.0}, "drill": {"feed": 120.0, "rpm": 5000.0, "peck": 3.0, "extra": 0.5},
             "levels": {"clearance": 50.0, "safety": 3.0}}
    p = autocam.plan(sheet(), 2, TOOLS, saved, 15.0, margin=10.0, spacing=14.0)
    drill, mill = p["operations"][0]["settings"], p["operations"][-1]["settings"]
    assert drill["data"]["feed_z"] == 120.0 and drill["data"]["rpm"] == 5000.0 and drill["drill"] == {"extra": 0.5, "peck": 3.0}
    assert mill["data"] == {"feed_xy": 1800.0, "feed_z": 100.0, "rpm": 12000.0, "direction": "CW"} and "drill" not in mill
    for s in (drill, mill):
        assert s["thickness"] == 15.0 and s["sheet"] == 2 and s["autocam"] is True and s["autocam_sheet"] == 2
        assert s["levels"] == {"clearance": 50.0, "safety": 3.0, "upper": None, "depth": None}        # upper level and depth follow the thickness
    assert p["operations"][0]["lv"]["upper"] == 15.0 and p["operations"][0]["lv"]["depth"] == 15.0
    assert autocam.merged_defaults(None) == autocam.DEFAULTS


def test_the_program_of_a_sheet_has_every_operation_with_its_tool_and_the_drilling_cycles():
    p = autocam.plan(sheet(), 1, TOOLS, {"drill": {"peck": 5.0}}, 18.0, margin=10.0, spacing=14.0)
    text, plans = autocam.program("t.anc", sheet(), 1, p, 18.0, "proj", 3, 10.0, 14.0)
    g = [x.rstrip() for x in text.split("\r\n")]
    boxes = [g[i + 2] for i, x in enumerate(g) if x == "(              OPERATION INFORMATION             )"]
    assert [b.strip("() ") for b in boxes] == ["Sheet1-DRILL-DRILL 20", "Sheet1-DRILL-DRILL 8", "Sheet1-HOLES-END MILL 12.7", "Sheet1-HOLES-END MILL 3", "Sheet1-CONTOUR-END MILL 12.7"]
    assert [x for x in g if re.fullmatch(r"N\d+", x)] == ["N1", "N2", "N3", "N4", "N5"]
    assert [x for x in g if x.startswith("M6 ")] == ["M6 T4", "M6 T5", "M6 T1", "M6 T3", "M6 T1"]       # a tool change wherever the tool changes
    assert g.count("M1") == 4 and "M5" not in g[:-8]                                                    # M1 + safe position before every tool change, no M5
    i = g.index("N2")
    assert g[i + 1:i + 5] == ["M1", "G0 G28 G91 Z0.", "G90", "G56"] and g[i + 5] == "M6 T5"
    assert g.count("M96 (BRUSHES DOWN)") == 1 and min(i for i, x in enumerate(g) if x.startswith("G1 ")) < g.index("M96 (BRUSHES DOWN)")   # brushes only for milling
    assert not any(x.startswith("M97") for x in g[:g.index("N3")])                                      # none after the drilling operations
    # the drilling operation like the SolidCAM post: rapid to the hole, the cycle without X and Y, G80, G05.1 Q0
    k = g.index("G0 G43 H4 Z45.")
    assert g[k + 1:k + 5] == ["G0 X120. Y150. Z45.", "G81 G98 Z0. R20 Q5. F150.".replace("G81", "G83"), "G80", "G05.1 Q0 (AICC/AIAPC HIGH SPEED MODE OFF)"]
    assert g.count("G80") == 2                                                                          # one per drilling operation
    assert text.startswith("%\r\n<t.anc>") and text.endswith("%")
    assert min(i for i, x in enumerate(g) if "G83" in x) < min(i for i, x in enumerate(g) if x.startswith("G1 "))   # drilling before milling


def test_a_drilling_cycle_is_written_once_and_the_next_holes_only_have_x_and_y():
    t = cam.Tool(number=4, name="DRILL", diameter=6.0, flutes=2, rpm=6000, feed=100, plunge=100, length=100)
    moves = [("G0", 10, 10, 45, None), ("G81", 10, 10, 0.0, 120, 17.0, 0.0), ("G81", 50, 30, 0.0, 120, 17.0, 0.0), ("G81", 80, 30, 0.0, 120, 17.0, 0.0)]
    text = post_fanuc.program("a.anc", [(t, moves)], cam.CamJob(thickness=18), 1, (150, 80), solidcam=True, op_names=["x"])
    g = [x.rstrip() for x in text.split("\r\n")]
    i = g.index("G81 G98 Z0. R17 F120.")                                                                # R without a decimal point, as SolidCAM
    assert g[i - 1] == "G0 X10. Y10. Z45." and g[i + 1:i + 5] == ["X50. Y30.", "X80. Y30.", "G80", "G05.1 Q0 (AICC/AIAPC HIGH SPEED MODE OFF)"]
    assert not any("G05.1 Q1" in x for x in g)                                                          # no smoothing ON for drilling
    assert cam.estimate_minutes([(t, moves)]) > 0


def test_an_operation_with_a_list_of_contours_cuts_only_those_and_a_drill_operation_drills():
    sh = sheet()
    t = tool(1, "END MILL", 6.0)
    data = {"feed_xy": 2500.0, "feed_z": 100.0, "rpm": 10000.0, "direction": "CW"}
    lv = {"clearance": 45.0, "safety": 2.0, "upper": 18.0, "depth": 18.0}
    ids = [c["id"] for c in cam.scan_contours(sh) if c["kind"] == "outer"]
    plan, groups, _t, _j = gcode_cam.build(sh, t, data, lv, 18.0, 10.0, 14.0, None, ids[:1])
    assert [c.kind for c in plan.contours] == ["outer"] and len(groups) == 1                           # one outline only
    holes = [c["id"] for c in cam.scan_contours(sh) if c["kind"] == "inner" and c["circle"]]
    plan, groups, _t, _j = gcode_cam.build(sh, tool(4, "DRILL", 20.0), data, lv, 18.0, 10.0, 14.0, None, holes, {"extra": 0.0, "peck": 0.0})
    cycles = [m for m in groups[0][1] if m[0] == "G81"]
    assert len(cycles) == 3 and all(m[3] == 0.0 and m[5] == 20.0 for m in cycles)                       # down to the bottom, R plane = upper + safety
    assert [m[1] for m in cycles] == sorted(m[1] for m in cycles)                                       # from left to right


def uneven_circle(cx, cy, r, angles_deg):
    """A circle cut into straight pieces at uneven angles (like a circle that was imported as a few arcs)."""
    return [(cx + r * math.cos(math.radians(a)), cy + r * math.sin(math.radians(a))) for a in angles_deg]


def test_a_circle_with_unevenly_spaced_vertices_is_still_a_circle_but_a_square_or_a_slot_is_not():
    angles = [0, 5, 10, 20, 40, 80, 120, 150, 170, 180, 200, 230, 260, 290, 320, 350]       # many vertices on one side: their centroid is off
    c = cam._circle_of(uneven_circle(70.0, 22.0, 3.175, angles) + [uneven_circle(70.0, 22.0, 3.175, [0])[0]])
    assert c and abs(c[0] - 70.0) < 1e-6 and abs(c[1] - 22.0) < 1e-6 and abs(c[2] - 3.175) < 1e-6
    assert cam._circle_of([(0, 0), (10, 0), (10, 10), (0, 10), (0, 0)]) is None
    slot = list(box(0, 0, 40, 10).buffer(0).exterior.coords)
    assert cam._circle_of(slot) is None
    half = uneven_circle(0, 0, 5.0, range(0, 181, 10))                                         # only half a circle
    assert cam._circle_of(half) is None


def test_a_hole_slightly_off_a_drill_diameter_is_drilled_within_the_tolerance_and_the_message_says_why_otherwise():
    hole = Point(70.0, 22.0).buffer(3.125, 32)                                                   # D 6.25
    sh = {"width": 300.0, "height": 100.0, "placements": [pl("PART6", box(0, 0, 150, 50).difference(hole))]}
    tools = [tool(1, "END MILL", 12.7), tool(4, "DRILL", 6.35)]
    p = autocam.plan(sh, 1, tools, None, 18.0, margin=10.0, spacing=14.0)                      # 0.1 mm apart: within the default tolerance
    assert [o["kind"] for o in p["operations"]] == ["drill", "outline"] and not p["skipped"]
    p = autocam.plan(sh, 1, tools, {"drill": {"tolerance": 0.05}}, 18.0, margin=10.0, spacing=14.0)
    assert [o["kind"] for o in p["operations"]] == ["outline"]
    assert len(p["skipped"]) == 1 and "T4" in p["skipped"][0] and "tolerance" in p["skipped"][0] and "6.25" in p["skipped"][0]
    p = autocam.plan(sh, 1, [tool(1, "END MILL", 12.7)], None, 18.0, margin=10.0, spacing=14.0)
    assert "no drill" in p["skipped"][0]
    square = {"width": 300.0, "height": 100.0, "placements": [pl("S", box(0, 0, 150, 50).difference(box(60, 20, 66, 26)))]}
    assert "not round" in autocam.plan(square, 1, tools, None, 18.0, margin=10.0, spacing=14.0)["skipped"][0]


def test_the_operation_is_named_after_the_tool_name_that_was_typed_when_the_tool_was_defined():
    tools = [tool(1, "END MILL", 12.7, '1/2" END MILL'), tool(4, "DRILL", 20.0, '20 mm DRILL')]
    p = autocam.plan(sheet(), 3, tools, None, 18.0, margin=10.0, spacing=14.0)
    assert [o["name"] for o in p["operations"]][0] == "Sheet3-DRILL-20 mm DRILL"
    assert [o["name"] for o in p["operations"]][-1] == 'Sheet3-CONTOUR-1/2" END MILL'
    p = autocam.plan(sheet(), 1, [tool(1, "END MILL", 12.7, " ")], None, 18.0, margin=10.0, spacing=14.0)       # no name typed: the tool number
    assert [o["name"] for o in p["operations"]][-1] == "Sheet1-CONTOUR-T1"
    p = autocam.plan(sheet(), 1, [tool(1, "END MILL", 12.7, "MILL"), tool(2, "END MILL", 3.0, "MILL")], None, 18.0, margin=10.0, spacing=14.0)
    names = [o["name"] for o in p["operations"]]
    assert len({n.lower() for n in names}) == len(names)                                                      # two tools with one name: still unique
