"""NestoCAM step 5: the G-code made from the four steps."""
import math
import pathlib
import re
import sys
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
from shapely.geometry import Point, box

import gcode_cam
from shapes import geom_to_json

TOOL = {"number": 1, "name": '1/2" END MILL', "diameter": 12.7, "flutes": 3, "cutting_length": 34.8}
DATA = {"feed_xy": 2500.0, "feed_z": 100.0, "lead_in": 50.0, "lead_out": 80.0, "rpm": 10000.0, "direction": "CW"}
LV = {"clearance": 45.0, "safety": 2.0, "upper": 15.0, "depth": 15.0}


def pl(name, g):
    b = g.bounds
    return {"part": name, "x": b[0], "y": b[1], "w": b[2] - b[0], "h": b[3] - b[1], "polys": geom_to_json(g)}


def sheet():
    return {"width": 1000.0, "height": 600.0, "placements": [
        pl("A", box(50, 50, 350, 250).difference(box(120, 100, 220, 190))),
        pl("B", box(420, 50, 700, 250)),
        pl("C", Point(850, 300).buffer(60, 32))]}


def gcode(data=DATA, lv=LV):
    return gcode_cam.text("p_sheet_01.anc", sheet(), 1, TOOL, data, lv, 15.0, "proj", 3, 5.0, 14.0)


def test_program_follows_the_post_and_the_levels():
    g = gcode().splitlines()
    assert g[0] == "%" and g[-1] == "%" and "M30" in g
    assert "M6 T1" in g and "G56" in g and "S10000 M3" in g and "G0 G43 H1 Z45." in g
    assert any("Z0 = BOTTOM OF THE PARTS" in x for x in g)
    zs = [float(m.group(1)) for x in g for m in [re.search(r" Z(-?[\d.]+)", x)] if m and x.startswith(("G0 ", "G1 ")) and "G28" not in x]
    assert max(zs) == 45.0 and min(zs) == 0.0                 # rapid at the clearance level, the cut goes down to upper - depth = 0
    assert any(x.startswith("G0") and x.endswith("Z17.") for x in g)       # rapid down to upper level + safety distance
    assert any(x.startswith("G1") and "Z0." in x and "F100." in x for x in g)    # then feed Z down to the bottom of the cut


def test_feeds_lead_in_and_lead_out_are_percent_of_the_feed_xy():
    g = gcode().splitlines()
    feeds = {m.group(1) for x in g for m in [re.search(r"F([\d.]+)", x)] if m}
    assert {"100.", "1250.", "2500.", "2000."} <= feeds       # feed Z, lead in 50 %, feed XY, lead out 80 %


def test_cut_depth_follows_the_profile_depth_and_the_upper_level():
    g = gcode(lv={**LV, "upper": 20.0, "depth": 12.0, "clearance": 50.0}).splitlines()
    assert any(x.startswith("G1") and "Z8." in x and "F100." in x for x in g)           # 20 - 12
    assert any(x.startswith("G0") and x.endswith("Z22.") for x in g)                     # 20 + 2


def test_ccw_spin_uses_m4_and_reverses_the_contour_direction():
    cw, ccw = gcode().splitlines(), gcode(data={**DATA, "direction": "CCW"}).splitlines()
    assert "S10000 M4" in ccw and "S10000 M3" in cw
    assert [x for x in cw if x.startswith("G1")] != [x for x in ccw if x.startswith("G1")]


def test_every_part_is_cut_holes_first_and_the_tool_stays_outside_the_parts():
    plan, groups, tool, _job = gcode_cam.build(sheet(), TOOL, DATA, LV, 15.0)
    assert [c.kind for c in plan.contours] == ["inner", "outer", "outer", "outer"]
    r = tool.diameter / 2
    # the path of the tool centre of an outline is a radius away from the part, the hole path a radius inside the hole
    from shapely.geometry import LineString
    outline_a = next(c for c in plan.contours if c.kind == "outer" and c.part == "A")
    assert abs(LineString(outline_a.pts).distance(box(50, 50, 350, 250)) - r) < 0.05
    moves = groups[0][1]
    assert all(0.0 - 1e-9 <= m[3] <= 45.0 + 1e-9 for m in moves)


def test_problems_and_warnings():
    assert gcode_cam.problems(None, DATA, LV)                                         # no tool chosen
    assert gcode_cam.problems(TOOL, {**DATA, "rpm": 20000.0}, LV)                      # more than the spindle
    assert gcode_cam.problems(TOOL, DATA, {**LV, "clearance": 10.0})                    # clearance below the upper level
    assert gcode_cam.problems(TOOL, DATA, LV) == []
    assert gcode_cam.warnings(TOOL, {**LV, "depth": 40.0, "upper": 40.0})               # deeper than the cutting length
    assert gcode_cam.warnings(TOOL, LV) == []


def test_generate_tab_shows_the_gcode_from_the_saved_steps():
    import types
    from streamlit.testing.v1 import AppTest
    saved_tool = {**TOOL, "tool_type": "END MILL", "shoulder_diameter": 12.7, "arbor_diameter": 12.7, "shoulder_angle": 0.0,
                  "shoulder_length": 43.7, "total_length": 43.7, "chamfer": 0.0, "outside_holder": 122.2, "helical_angle": 30.0}
    cam = {"camming": {"thickness": 15.0, "tool": 1, "data": DATA, "levels": {"clearance": 45.0, "safety": 2.0, "upper": None, "depth": None}}}
    db = types.ModuleType("db")
    db.list_cam_tools = lambda: [saved_tool]
    db.get_cam = lambda pid: cam
    db.save_cam = lambda *a: None
    sys.modules["db"] = db
    try:
        def app():
            from camming_ui import camming_tab
            import db
            import test_gcode
            camming_tab(db, 1, [test_gcode.sheet()], "proj", {"margin": 5.0, "spacing": 14.0}, 3, "proj_run3")
        sys.path.insert(0, str(pathlib.Path(__file__).parent))
        at = AppTest.from_function(app, default_timeout=60).run()
        assert not at.exception
        assert [t.label for t in at.tabs][4] == "5 · Generate G-code"
        code = "\n".join(c.value for c in at.code)
        assert "M6 T1" in code and "G0 G43 H1 Z45." in code and "S10000 M3" in code
        assert not at.error
        # a tool that is not chosen: an error and no G-code
        cam["camming"]["tool"] = None
        at = AppTest.from_function(app, default_timeout=60).run()
        assert at.error and not any("M6 T1" in c.value for c in at.code)
    finally:
        sys.modules.pop("db", None)
