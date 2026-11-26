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
DATA = {"feed_xy": 2500.0, "feed_z": 100.0, "rpm": 10000.0, "direction": "CW"}
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
    """The program as lines, without the space at the end of the blocks."""
    return [x.rstrip() for x in gcode_cam.text("p_sheet_01.anc", sheet(), 1, TOOL, data, lv, 15.0, "proj", 3, 5.0, 14.0).split("\r\n")]


def test_program_follows_the_post_and_the_levels():
    g = gcode()
    assert g[0] == "%" and g[-1] == "%" and "M30" in g
    assert "M6 T1" in g and "G56" in g and "S10000 M3" in g and "G0 G43 H1 Z45." in g
    zs = [float(m.group(1)) for x in g for m in [re.search(r" Z(-?[\d.]+)", x)] if m and x.startswith(("G0 ", "G1 ")) and "G28" not in x]
    assert max(zs) == 45.0 and min(zs) == 0.0                 # rapid at the clearance level, the cut goes down to upper - depth = 0
    assert any(x.startswith("G0") and x.endswith("Z17.") for x in g)       # rapid down to upper level + safety distance
    assert any(x.startswith("G1") and "Z0." in x and "F100." in x for x in g)    # then feed Z down to the bottom of the cut


def test_no_lead_in_or_out_the_loop_starts_and_ends_at_the_same_point_with_brushes_like_the_post():
    g = gcode()
    feeds = {m.group(1) for x in g if x.startswith("G1") for m in [re.search(r"F([\d.]+)", x)] if m}
    assert feeds == {"100.", "2500."}                                  # only feed Z and feed XY
    plunge = [i for i, x in enumerate(g) if x.startswith("G1 Z0. F100.")]
    assert len(plunge) == 4                                            # one plunge per contour: 1 hole + 3 outlines
    assert g[plunge[0] + 1] == "M96 (BRUSHES DOWN)" and g.count("M96 (BRUSHES DOWN)") == 1   # after the first cutting move only
    assert g[g.index("M5") - 1] == "M97 (BRUSHES UP)"
    for i in plunge:                                                   # the contour ends where it started (it is closed)
        j = i
        while not g[j].startswith("G0 Z45."):
            j += 1
        assert g[j - 1].startswith("G1") and g[j] == "G0 Z45."


def test_cut_depth_follows_the_profile_depth_and_the_upper_level():
    g = gcode(lv={**LV, "upper": 20.0, "depth": 12.0, "clearance": 50.0})
    assert any(x.startswith("G1") and "Z8." in x and "F100." in x for x in g)           # 20 - 12
    assert any(x.startswith("G0") and x.endswith("Z22.") for x in g)                     # 20 + 2


def test_ccw_spin_uses_m4_and_reverses_the_contour_direction():
    cw, ccw = gcode(), gcode(data={**DATA, "direction": "CCW"})
    assert "S10000 M4" in ccw and "S10000 M3" in cw
    assert [x for x in cw if x.startswith("G1")] != [x for x in ccw if x.startswith("G1")]


def test_contours_start_at_the_lowest_left_vertex_and_go_from_left_to_right():
    big = {"width": 6250.0, "height": 1500.0, "placements": [
        pl("R2", box(2429.0, 5, 4569.0, 1195)), pl("R1", box(5, 5, 2416, 1195)), pl("S", box(5, 1208, 400, 1465))]}
    plan, groups, tool, _ = gcode_cam.build(big, TOOL, DATA, LV, 15.0)
    r = tool.diameter / 2
    assert [c.part for c in plan.contours] == ["R1", "S", "R2"]                       # lower left first, then left to right
    r1 = plan.contours[0].pts
    assert r1[0] == (5 - r, 5) and r1[1] == (5 - r, 1195)                                # the lowest left vertex, going up the left edge
    s = plan.contours[1].pts
    assert s[0] == (5 - r, 1208) and s[1] == (5 - r, 1465)


def test_arcs_are_within_0_02_mm():
    from shapely.geometry import LineString, Point
    plan, _g, tool, _ = gcode_cam.build({"width": 1000.0, "height": 600.0, "placements": [pl("A", box(50, 50, 350, 250))]}, TOOL, DATA, LV, 15.0)
    r = tool.diameter / 2
    pts = plan.contours[0].pts
    corner = Point(50, 50)
    arc = [p for p in pts if p[0] < 50 and p[1] < 50]
    assert arc and all(abs(math.hypot(p[0] - 50, p[1] - 50) - r) < 1e-6 for p in arc)           # the points are on the arc ...
    seg = [(a, b) for a, b in zip(pts, pts[1:]) if a[0] < 50 and a[1] < 50 and b[0] < 50 and b[1] < 50]
    assert max(r - LineString(s).distance(corner) for s in seg) <= 0.02 + 1e-9                  # ... and the chords are close to it


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


def test_generate_tab_shows_the_gcode_of_the_operation_and_names_the_file_after_it():
    import types
    from streamlit.testing.v1 import AppTest
    saved_tool = {**TOOL, "tool_type": "END MILL", "shoulder_diameter": 12.7, "arbor_diameter": 12.7, "shoulder_angle": 0.0,
                  "shoulder_length": 43.7, "total_length": 43.7, "chamfer": 0.0, "outside_holder": 122.2, "helical_angle": 30.0}
    settings = {"sheet": 1, "thickness": 15.0, "tool": 1, "data": DATA, "levels": {"clearance": 45.0, "safety": 2.0, "upper": None, "depth": None}}
    db = types.ModuleType("db")
    db.list_cam_tools = lambda: [saved_tool]
    db.get_operation = lambda i: {"id": i, "run_id": 1, "name": "sheet_01 Milling", "settings": settings}
    db.save_operation_settings = lambda *a: None
    sys.modules["db"] = db
    try:
        def app():
            from operations_ui import operation_view
            import db
            import test_gcode
            operation_view(db, {"id": 7, "name": "sheet_01 Milling", "settings": {}}, [test_gcode.sheet()], "proj",
                           {"margin": 5.0, "spacing": 14.0}, 3)
        sys.path.insert(0, str(pathlib.Path(__file__).parent))
        at = AppTest.from_function(app, default_timeout=60).run()
        assert not at.exception
        assert [t.label for t in at.tabs][4] == "5 · Generate G-code"
        code = "\n".join(c.value for c in at.code)
        assert "M6 T1" in code and "G0 G43 H1 Z45." in code and "S10000 M3" in code
        assert "<sheet_01_Milling.anc>" in code and "(sheet_01 Milling" in code
        assert code.rstrip().endswith("%") and "\n..." not in code and "\r" not in code           # the whole program, not a part of it
        assert not next(e for e in at.expander if e.label.startswith("G-code of")).proto.expanded   # hidden until it is opened      # the file name is the operation name
        assert not at.error
        settings["tool"] = None                                                                   # no tool chosen: an error and no G-code
        at = AppTest.from_function(app, default_timeout=60).run()
        assert at.error and not any("M6 T1" in c.value for c in at.code)
    finally:
        sys.modules.pop("db", None)


def test_file_stem():
    from operations_ui import file_stem
    assert file_stem("sheet_01 Milling") == "sheet_01_Milling" and file_stem("  ../x/y?  ") == "x_y" and file_stem("???") == "operation"


def test_custom_order_is_followed_by_the_toolpaths_and_the_gcode():
    import camming_ui as cu
    ch = cu.chains(sheet())
    dflt = cu.default_order(ch)
    assert [c["type"] for c in ch if c["id"] in dflt[:1]] == ["Hole"]                 # the default: the hole first
    ids = {c["part"]: c["id"] for c in ch if c["type"] == "Outline"}
    order = [ids["C"], ids["B"], dflt[0], ids["A"]]                                    # my own order: C, B, the hole of A, A
    plan, _g, _t, _j = gcode_cam.build(sheet(), TOOL, DATA, LV, 15.0, order_ids=order)
    assert [(c.kind, c.part) for c in plan.contours] == [("outer", "C"), ("outer", "B"), ("inner", "A"), ("outer", "A")]
    g = gcode_cam.text("x.anc", sheet(), 1, TOOL, DATA, LV, 15.0, order_ids=order).split("\r\n")
    plunges = [i for i, x in enumerate(g) if x.startswith("G1 Z0. F100.")]
    assert len(plunges) == 4
    # the contours that are not in the saved order come after it, in the default order (holes first)
    plan2, _g, _t, _j = gcode_cam.build(sheet(), TOOL, DATA, LV, 15.0, order_ids=[ids["C"]])
    assert [(c.kind, c.part) for c in plan2.contours] == [("outer", "C"), ("inner", "A"), ("outer", "A"), ("outer", "B")]


def test_full_order_drops_what_is_gone_and_adds_what_is_new():
    import camming_ui as cu
    ch = cu.chains(sheet())
    d = cu.default_order(ch)
    assert cu.full_order(ch, None) == d
    assert cu.full_order(ch, ["O@9.00,9.00", d[2], d[0]]) == [d[2], d[0], d[1], d[3]]


def test_geometry_step_lists_the_contours_in_order_and_moves_them():
    import types
    from streamlit.testing.v1 import AppTest
    settings = {"sheet": 1, "thickness": 15.0}
    db = types.ModuleType("db")
    db.list_cam_tools = lambda: []
    db.get_operation = lambda i: {"id": i, "run_id": 1, "name": "op", "settings": settings}
    db.save_operation_settings = lambda i, s: (settings.clear(), settings.update(s))
    sys.modules["db"] = db
    try:
        def app():
            from operations_ui import operation_view
            import db
            import test_gcode
            operation_view(db, {"id": 7, "name": "op", "settings": {}}, [test_gcode.sheet()], "proj", {}, 3)
        sys.path.insert(0, str(pathlib.Path(__file__).parent))
        at = AppTest.from_function(app, default_timeout=60).run()
        assert not at.exception
        import camming_ui as cu
        ch = cu.chains(sheet())
        d = cu.default_order(ch)
        assert all(b.disabled for b in at.button if b.key.startswith("geo_move_"))               # no row selected yet: nothing to move
        at.session_state["geo_move_sel"] = d[0]                                                   # (a click on a row of the table)
        at.run()
        assert not any(b.disabled for b in at.button if b.key.startswith("geo_move_"))
        save = lambda: next(b for b in at.button if b.key == "geo_order_save")                 # noqa: E731
        assert save().disabled                                                                  # nothing to save yet
        next(b for b in at.button if b.key == "geo_move_last").click().run()               # the hole goes last ...
        assert "order" not in settings and not save().disabled                                 # ... on the working copy: nothing saved yet
        assert at.session_state["geo_move_sel"] == d[0]                                           # it stays selected (and highlighted)
        assert any(f"{len(d)} · A (hole)" in m.value for m in at.markdown) or any(f"{len(d)} · A (hole)" in c.value for c in at.caption)
        assert any("not saved yet" in w.value for w in at.warning)
        next(b for b in at.button if b.key == "geo_move_up").click().run()
        assert "order" not in settings
        save().click().run()                                                                    # Save order stores the working order
        assert settings["order"] == d[1:-1] + [d[0]] + [d[-1]] and settings["order_sheet"] == 1
        assert save().disabled and not any("not saved yet" in w.value for w in at.warning)    # saved: the working copy is the saved order
        next(b for b in at.button if b.key == "geo_order_reset").click().run()               # the default order, still to be saved
        assert settings["order"] == d[1:-1] + [d[0]] + [d[-1]]
        next(b for b in at.button if b.key == "geo_order_discard").click().run()             # discard: back to the saved order
        assert save().disabled
        next(b for b in at.button if b.key == "geo_order_reset").click().run()
        save().click().run()
        assert "order" not in settings                                                          # the default order is not stored
    finally:
        sys.modules.pop("db", None)


def test_the_program_is_written_like_the_solidcam_file():
    tool = {**TOOL, "name": '1/2" END MILL', "outside_holder": 122.237, "holder": "ADAPTOR"}
    raw = gcode_cam.text("sheet_01_Milling.anc", sheet(), 1, tool, DATA, LV, 15.0, "proj", 3, 5.0, 14.0, "F-CONTOUR")
    assert raw.startswith("%\r\n<sheet_01_Milling.anc>\r\n") and raw.endswith("\r\n%")          # CR LF, nothing after the last %
    assert "\n" not in raw.replace("\r\n", "")
    g = raw.split("\r\n")
    box = "(" + "-" * 48 + ")"
    assert g[2:5] == [box, "(           POST PROCESSOR INFORMATION           )", box]
    assert g[5] == "(CAM VERSION    : NESTOCAM 2026                  )"
    assert g[6:11] == ["(LICENSED TO    : MARIANI                        )", "(MACHINE        : CR ONSRUD 336G18CW7            )",
                       "(CONTROLLER     : FANUC                          )", "(POST VERSION   : 0.4                            )",
                       "(POST CREATED BY: SSG                            )"]
    assert g[11:15] == [box, "(               PROGRAM INFORMATION              )", box, "(PART NAME   : SHEET_01_MILLING                  )"]
    assert re.fullmatch(r"\(G-CODE DATE : \d\d-[A-Z]{3}-\d{4} {23}\)", g[15]) and re.fullmatch(r"\(G-CODE TIME : \d\d:\d\d:\d\d {26}\)", g[16])
    assert re.fullmatch(r"\(EST M/C TIME: \d\d:\d\d:\d\d {26}\)", g[17])
    assert g[18:26] == [box, "(                    TOOL LIST                   )", box, "(TOOL|              TOOL              |   TOOL   )",
                        "( NO |           DESCRIPTION          |  LENGTH  )", box, '(  1 | 1/2" END MILL 3F               |  122mm   )', box]
    k = g.index("G90 G21 G40 G49 G80 G94 G98 G17")
    assert g[k + 1:k + 4] == ["(METRIC PROGRAM)", "G0 G28 G91 Z0. ", "G90"]                      # a space ends a block
    i = g.index("(              OPERATION INFORMATION             )")
    assert g[i + 2:i + 12] == ["(F-CONTOUR                                       )", box, box, "(                TOOL INFORMATION                )", box,
                               '(1/2" END MILL 3F                                )', "(TOOL LENGTH: 122.24mm                           )",
                               "(HOLDER: ADAPTOR                                 )", box, "N1 "]
    assert g[i + 12:i + 16] == ["G56 ", "M6 T1 ", "G0 C0. ", "S10000 M3 "]
    assert g[i + 16].startswith("G94 G0 X") and g[i + 16].endswith(" ")                         # G94 and the first rapid on one line
    assert "M96 (BRUSHES DOWN)" in g and "G05.1 Q1 R7 (AICC/AIAPC HIGH SPEED MODE ON)" in g    # comment lines: no space at the end
    assert g[-11:] == ["G0 Z45. ", "G05.1 Q0 (AICC/AIAPC HIGH SPEED MODE OFF)", "G0 G28 G91 Z0. ", "G90", "M97 (BRUSHES UP)", "M5 ",
                       "G0 G28 G91 Z0. ", "G90", "G0 C0. ", "M30 ", "%"]
    for x in g:                                       # every block that is not a comment line, a G90 line or the % ends with a space
        if x and not x.startswith(("(", "%", "<")) and "(" not in x and x != "G90" and not x.startswith("G90 G21"):
            assert x.endswith(" "), x


def test_no_holder_no_holder_line():
    raw = gcode_cam.text("a.anc", sheet(), 1, {**TOOL, "holder": ""}, DATA, LV, 15.0)
    assert "HOLDER" not in raw


def test_arcs_have_equal_pieces_like_solidcam():
    import cam
    r = 6.35
    ring = cam.offset_ring([(5, 5), (2416, 5), (2416, 1195), (5, 1195), (5, 5)], r, True, 0.01)
    assert len(ring) == 61                                                          # 4 x (14 pieces + 1 point) + the closing point
    ring = cam._restart_at(cam._orient(ring, False), (5 - r, 5))
    assert ring[0] == (5 - r, 5) and ring[1] == (5 - r, 1195)                       # the straight edge up the left side
    assert abs(ring[2][0] - (-1.3103)) < 1e-3 and abs(ring[2][1] - 1195.711) < 1e-3  # the first arc point, as in the SolidCAM file
    arc = ring[1:16]                                                                # the pieces of one arc are all the same length
    d = [math.hypot(a[0] - b[0], a[1] - b[1]) for a, b in zip(arc, arc[1:])]
    assert max(d) - min(d) < 1e-6


def test_the_header_date_and_time_are_canada_eastern_time():
    import datetime
    import post_fanuc
    utc = datetime.timezone.utc
    assert post_fanuc.local_time(datetime.datetime(2026, 11, 27, 0, 30, tzinfo=utc)).strftime("%d-%b-%Y %H:%M:%S") == "26-Nov-2026 19:30:00"   # winter: UTC-5
    assert post_fanuc.local_time(datetime.datetime(2026, 7, 1, 16, 0, tzinfo=utc)).strftime("%H:%M") == "12:00"                               # summer: UTC-4
    raw = gcode_cam.text("a.anc", sheet(), 1, TOOL, DATA, LV, 15.0)
    m = re.search(r"G-CODE TIME : (\d\d):(\d\d):(\d\d)", raw)
    now = post_fanuc.local_time()
    assert abs((int(m.group(1)) * 60 + int(m.group(2))) - (now.hour * 60 + now.minute)) <= 1 or abs(int(m.group(1)) - now.hour) in (0, 23)
