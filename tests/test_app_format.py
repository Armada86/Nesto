"""The app shows utilisation percentages with two decimals (runs list, overall result, every sheet title)."""
import re
import sys
import pathlib
import types
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
import pytest

APP = str(pathlib.Path(__file__).parent.parent / "app.py")


def test_sheet_title_has_two_decimals():
    from nesting import Placement, Sheet
    from render import draw_sheet
    import matplotlib.pyplot as plt
    sh = Sheet(1000, 500, [Placement("a", 0, 0, 333, 301, False)])           # 100233 / 500000 = 20.0466 %
    fig = draw_sheet(sh, "Sheet 1")
    title = fig.axes[0].get_title()
    assert "20.05% used" in title and not re.search(r"\d\.\d%", title)
    plt.close(fig)


@pytest.fixture()
def fake_db(monkeypatch):
    runs = [{"id": 7, "sheets": 3, "utilization": 0.755312, "seconds": 12.0},
            {"id": 6, "sheets": 2, "utilization": 0.5, "seconds": 3.0}]
    parts = [{"name": "a", "shape": "rectangle", "width": 300.0, "height": 200.0, "qty": 3, "rotate": True, "outline": None}]
    db = types.ModuleType("db")
    db.get_url = lambda: "x"
    db.init_db = lambda: None
    db.list_projects = lambda: [{"id": 1, "name": "P"}]
    db.get_project = lambda i: {"id": i, "name": "P", "settings": None}
    db.load_parts = lambda i: [dict(p) for p in parts]
    db.save_parts = lambda *a: None
    db.latest_run_settings = lambda pid: {}
    db.save_run = lambda *a, **k: None
    db.list_runs = lambda i: list(runs)
    db.load_run = lambda i: None
    db.delete_runs = lambda *a: 0
    db.list_tools = lambda: []
    db.save_tools = lambda *a: None
    db.get_cam = lambda i: None
    db.save_cam = lambda *a: None
    db.list_cam_tools = lambda: []
    db.save_cam_tool = lambda *a, **k: None
    db.delete_cam_tool = lambda *a: None
    ops = {}                                                                  # the operations of the runs, in memory

    def create_operation(run_id, name, settings):
        if any(o["name"].lower() == name.lower() and o["run_id"] == run_id for o in ops.values()):
            raise ValueError("exists")
        i = len(ops) + 1
        ops[i] = {"id": i, "run_id": run_id, "name": name, "settings": settings}
        return i
    db.create_operation = create_operation
    db.list_operations = lambda run_id: [o for o in ops.values() if o["run_id"] == run_id]
    db.get_operation = lambda i: ops.get(i)
    db.save_operation_settings = lambda i, s: ops[i].__setitem__("settings", s)
    db.latest_operation_settings = lambda pid: None
    db.get_app_setting = lambda name: None
    db.save_app_setting = lambda name, value: None

    def delete_autocam_operations(run_id, sheet_no):
        gone = [i for i, o in ops.items() if o["run_id"] == run_id and (o["settings"] or {}).get("autocam_sheet") == sheet_no]
        for i in gone:
            del ops[i]
        return len(gone)
    db.delete_autocam_operations = delete_autocam_operations
    db.rename_operation = lambda i, n: ops[i].__setitem__("name", n)
    db.delete_operation = lambda i: ops.pop(i, None)
    monkeypatch.setitem(sys.modules, "db", db)
    return db


def test_runs_list_and_result_show_two_decimals(fake_db):
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(APP, default_timeout=60)
    at.session_state["pid"] = 1
    at.session_state["pending_settings"] = {"effort_label": "Quick"}
    at.run()
    assert not at.exception
    options = [o for s in at.selectbox for o in s.options]
    assert any("75.53%" in o for o in options) and any("50.00%" in o for o in options)
    next(b for b in at.button if b.label.endswith("Nest")).click().run()
    assert not at.exception
    util = next(m.value for m in at.metric if m.label == "Utilization")
    assert re.fullmatch(r"\d+\.\d{2}%", util), util


def test_sheet_size_is_labelled_length_and_width(fake_db):
    """The sheet is defined by its length (horizontal side) and its width (vertical side)."""
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(APP, default_timeout=60)
    at.session_state["pid"] = 1
    at.session_state["pending_settings"] = {"sheet_w": 3000.0, "sheet_h": 1500.0}
    at.run()
    assert not at.exception
    assert at.number_input(key="sheet_w").label == "Length"
    assert at.number_input(key="sheet_h").label == "Width"
    assert "Height" not in [n.label for n in at.number_input]
    assert at.number_input(key="sheet_w").value == 3000.0      # the horizontal side is the length
    assert at.number_input(key="sheet_h").value == 1500.0      # the vertical side is the width


def _label_sheet():
    from nesting import Placement, Sheet
    from shapely.geometry import box
    from shapes import geom_to_json

    def pl(name, g):
        b = g.bounds
        return Placement(name, b[0], b[1], b[2] - b[0], b[3] - b[1], False, 0.0, g.area, geom_to_json(g))
    return Sheet(2500, 1250, [pl("BIG", box(200, 200, 2300, 1000)),
                              pl("MID", box(20, 1100, 160, 1160)),                  # smaller than BIG but still readable inside
                              pl("L1", box(5, 600, 25, 612)), pl("R1", box(2470, 300, 2490, 312)),
                              pl("T1", box(1200, 1230, 1220, 1243)), pl("B1", box(1200, 6, 1220, 19))])


def test_small_parts_get_their_name_outside_on_the_nearest_sheet_edge_big_parts_inside():
    from matplotlib.text import Annotation
    from render import draw_sheet
    import matplotlib.pyplot as plt
    fig = draw_sheet(_label_sheet(), "Sheet 1")
    ax = fig.axes[0]
    out = {t.get_text(): t.xyann for t in ax.texts if isinstance(t, Annotation)}
    inside = {t.get_text(): t for t in ax.texts if not isinstance(t, Annotation) and not t.get_text().startswith("scrap")}
    assert set(out) == {"L1", "R1", "T1", "B1"}                                        # the four tiny parts
    assert out["L1"][0] < 0 and out["R1"][0] > 2500 and out["T1"][1] > 1250 and out["B1"][1] < 0    # spread round the sheet
    assert {"BIG", "MID"} <= set(inside) and inside["BIG"].get_fontsize() == 7.0        # readable parts keep their name inside
    assert inside["MID"].get_fontsize() >= 5.0
    xl, yl = ax.get_xlim(), ax.get_ylim()
    assert xl[0] < 0 and xl[1] > 2500 and yl[0] < 0 and yl[1] > 1250                  # room was made on all four sides
    plt.close(fig)


def test_a_sheet_without_small_parts_has_no_outside_names_and_no_extra_room():
    from matplotlib.text import Annotation
    from render import draw_sheet
    import matplotlib.pyplot as plt
    sh = _label_sheet()
    sh.placements = sh.placements[:2]
    fig = draw_sheet(sh, "Sheet 1")
    assert not [t for t in fig.axes[0].texts if isinstance(t, Annotation)]
    assert abs(fig.axes[0].get_xlim()[1] - 2550) < 1e-6 and abs(fig.axes[0].get_xlim()[0] + 50) < 1e-6
    plt.close(fig)


def test_a_part_has_the_same_colour_on_every_sheet():
    from matplotlib.colors import to_hex
    from nesting import Placement, Sheet
    from render import draw_sheet, part_color_indices
    import matplotlib.pyplot as plt
    import sheet_editor as se

    def sheet(*names):
        return Sheet(1000, 500, [Placement(n, 10 + 160 * k, 10, 150, 100, False) for k, n in enumerate(names)])
    s1, s2 = sheet("A", "B", "C"), sheet("D", "C", "A")                  # "C" and "A" sit in other places on sheet 2
    idx = part_color_indices([s1.placements, s2.placements])
    assert idx == {"A": 0, "B": 1, "C": 2, "D": 3}                          # order of first appearance over the whole run

    def face(fig, k):
        return to_hex(fig.axes[0].patches[1 + k].get_facecolor(), keep_alpha=False)
    f1, f2 = draw_sheet(s1, "1", color_index=idx), draw_sheet(s2, "2", color_index=idx)
    assert face(f1, 0) == face(f2, 2)                                       # A on sheet 1 == A on sheet 2
    assert face(f1, 2) == face(f2, 1)                                       # C on sheet 1 == C on sheet 2
    assert len({face(f1, 0), face(f1, 1), face(f1, 2), face(f2, 0)}) == 4   # different parts, different colours
    plt.close(f1), plt.close(f2)
    # the edit view uses the same colours
    d1 = [{"part": p.part, "x": p.x, "y": p.y, "w": p.w, "h": p.h} for p in s1.placements]
    d2 = [{"part": p.part, "x": p.x, "y": p.y, "w": p.w, "h": p.h} for p in s2.placements]
    c1 = {x["name"]: x["color"] for x in se.parts_payload({"width": 1000, "height": 500, "placements": d1}, idx)}
    c2 = {x["name"]: x["color"] for x in se.parts_payload({"width": 1000, "height": 500, "placements": d2}, idx)}
    assert c1["A"] == c2["A"] and c1["C"] == c2["C"] and len(set(c1.values())) == 3


def test_nestocam_tab_sees_a_run_that_is_loaded_in_the_same_script_run(fake_db):
    """The NestoCAM tab is drawn after the History: loading a run must show its operations and, in an operation, its geometry."""
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(APP, default_timeout=60)
    at.session_state["pid"] = 1
    at.session_state["pending_settings"] = {"effort_label": "Quick"}
    at.run()
    assert any("Nest first" in i.value for i in at.info)                       # nothing nested yet: no run, no operations
    fake_db.load_run = lambda i: {"id": i, "sheet_w": 1000.0, "sheet_h": 600.0, "margin": 5.0, "spacing": 3.0, "seconds": 1.0,
                                  "result": [{"width": 1000.0, "height": 600.0, "placements": [
                                      {"part": "a", "x": 10.0, "y": 10.0, "w": 300.0, "h": 200.0, "rotated": False}]}]}
    next(b for b in at.button if b.label == "Load run").click().run()
    assert not at.exception
    assert not any("Nest first" in i.value for i in at.info)
    assert any("No operations in this run yet" in i.value for i in at.info)    # the run is loaded: its (empty) operation list
    assert "ops_new_name" not in [t.key for t in at.text_input]                # no name box until Create operation is pressed
    next(b for b in at.button if b.key == "ops_new").click().run()             # Create operation opens the name box
    at.text_input(key="ops_new_name").set_value("sheet_01_Milling")            # create an operation: it opens with its five steps
    next(b for b in at.button if b.key == "ops_create").click().run()
    assert next(b for b in at.button if b.key == "ops_new").disabled           # an operation is open: close it before creating another
    assert "ops_new_name" not in [t.key for t in at.text_input]                # and the name box is gone
    assert not at.exception
    assert [t.label for t in at.tabs][-5:] == ["1 · Geometry", "2 · Tools", "3 · Data", "4 · Levels", "5 · Generate G-code"]
    assert any(m.label == "Outlines" and m.value == "1" for m in at.metric)    # the geometry of the sheet of the operation
    assert any(s.value == "Run 7" for s in at.subheader)                # the loaded run's number above the run numbers
    assert [o["name"] for o in fake_db.list_operations(7)] == ["sheet_01_Milling"]
    next(b for b in at.button if b.key == "ops_close").click().run()
    assert not next(b for b in at.button if b.key == "ops_new").disabled
    next(b for b in at.button if b.key == "ops_new").click().run()
    at.text_input(key="ops_new_name").set_value("SHEET_01_milling")            # the same name (any capitals) twice in a run: refused
    next(b for b in at.button if b.key == "ops_create").click().run()
    assert any("exists" in e.value for e in at.error) and len(fake_db.list_operations(7)) == 1


def test_loading_a_run_brings_its_sheet_and_nesting_options_into_the_boxes_in_the_nesting_tab(fake_db):
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(APP, default_timeout=60)
    at.session_state["pid"] = 1
    at.session_state["pending_settings"] = {"effort_label": "Quick"}
    at.run()
    assert at.number_input(key="sheet_w").value == 2500.0 and at.select_slider(key="rot_label").value == "90°"
    fake_db.load_run = lambda i: {"id": i, "sheet_w": 3000.0, "sheet_h": 1500.0, "margin": 8.0, "spacing": 4.0, "seconds": 1.0,
                                  "options": {"rot_label": "15°", "prec_label": "Fine", "effort_label": "Thorough"},
                                  "result": [{"width": 3000.0, "height": 1500.0, "placements": []}]}
    next(b for b in at.button if b.label == "Load run").click().run()
    assert not at.exception
    assert [at.number_input(key=k).value for k in ("sheet_w", "sheet_h", "margin", "spacing")] == [3000.0, 1500.0, 8.0, 4.0]
    assert [at.select_slider(key=k).value for k in ("rot_label", "prec_label", "effort_label")] == ["15°", "Fine", "Thorough"]
    # an old run without options: the sheet comes back, the options stay as they are
    fake_db.load_run = lambda i: {"id": i, "sheet_w": 2000.0, "sheet_h": 1000.0, "margin": 5.0, "spacing": 3.0, "seconds": 1.0,
                                  "result": [{"width": 2000.0, "height": 1000.0, "placements": []}]}
    next(b for b in at.button if b.label == "Load run").click().run()
    assert at.number_input(key="sheet_w").value == 2000.0 and at.select_slider(key="rot_label").value == "15°"


def test_the_sheet_boxes_are_read_when_nest_is_pressed_and_a_part_that_does_not_fit_gives_a_message(fake_db):
    from streamlit.testing.v1 import AppTest
    runs = []
    fake_db.save_run = lambda *a, **k: runs.append((a, k)) or 99
    at = AppTest.from_file(APP, default_timeout=60)
    at.session_state["pid"] = 1
    at.session_state["pending_settings"] = {"effort_label": "Quick"}
    at.run()
    assert not at.exception
    assert not runs                                                            # nothing nests by itself
    at.number_input(key="sheet_w").set_value(1000.0)
    at.number_input(key="sheet_h").set_value(700.0)
    next(b for b in at.button if b.key == "nest_btn").click().run()          # Nest reads the numbers and stores them
    assert not at.exception
    assert runs and runs[-1][0][1:3] == (1000.0, 700.0)                        # the sheet and the options are stored with the run
    assert runs[-1][1]["options"]["effort_label"] == "Quick"
    at.number_input(key="sheet_w").set_value(100.0)                            # smaller than the parts (300 x 200)
    at.number_input(key="sheet_h").set_value(100.0)
    n = len(runs)
    next(b for b in at.button if b.key == "nest_btn").click().run()
    assert not at.exception and len(runs) == n                                 # no traceback, no run
    assert any("does not fit" in e.value for e in at.error)


def test_there_is_no_save_button_in_the_sidebar_and_the_parts_list_saves_itself(fake_db):
    from streamlit.testing.v1 import AppTest
    saved = []
    fake_db.save_parts = lambda pid, ps: saved.append([dict(p) for p in ps])
    at = AppTest.from_file(APP, default_timeout=60)
    at.session_state["pid"] = 1
    at.run()
    assert not at.exception
    assert not any(b.label == "Save" for b in at.sidebar.button)                 # the project is saved as it changes
    assert [b.label for b in at.sidebar.button] == ["New", "Load", "Rename", "Delete"]
    assert not saved                                                             # nothing changed: nothing is written
    at.session_state["parts_sig"] = "old"                                        # the table is different from what is stored
    at.run()
    assert saved and saved[-1][0]["name"] == "a" and saved[-1][0]["qty"] == 3


def test_parts_are_ticked_in_the_list_and_deleted_together_after_a_confirmation(fake_db):
    from streamlit.testing.v1 import AppTest
    saved = []
    fake_db.save_parts = lambda pid, ps: saved.append([p["name"] for p in ps])
    fake_db.load_parts = lambda i: [{"name": n, "shape": "rectangle", "width": 300.0, "height": 200.0, "qty": 1, "rotate": True,
                                     "flip": True, "outline": None} for n in ("a", "b", "c")]
    at = AppTest.from_file(APP, default_timeout=60)
    at.session_state["pid"] = 1
    at.run()
    assert not at.exception
    top = next(b for b in at.button if b.key == "lib_delete")
    assert not top.disabled                                                     # always active
    top.click().run()
    assert any(w.value == "No part selected." for w in at.warning) and not saved       # nothing ticked: a message
    at.checkbox(key="lib_chk_0").check()                                        # ticking does not run the script (it is in a form)
    at.checkbox(key="lib_chk_2").check()
    next(b for b in at.button if b.key == "lib_delete").click().run()           # the question comes first: nothing is deleted yet
    assert not saved and any("**a**" in w.value and "**c**" in w.value for w in at.warning)


def test_the_nesting_tab_titles(fake_db):
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(APP, default_timeout=60)
    at.session_state["pid"] = 1
    at.run()
    assert not at.exception
    titles = [s.value for s in at.subheader]
    assert {"Nesting history", "Sheet details", "Nesting options"} <= set(titles)
    assert not {"History", "Sheet", "Nesting"} & set(titles)


def test_cam_options_opens_a_screen_with_the_tool_library_and_goes_back(fake_db):
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(APP, default_timeout=60)
    at.session_state["pid"] = 1
    at.run()
    assert not at.exception
    next(b for b in at.button if b.key == "cam_options_btn").click().run()      # the button is there also before any run is loaded
    assert not at.exception
    assert any(s.value == "CAM options" for s in at.subheader)
    assert any(b.key == "tools_new" for b in at.button)                           # the tool library: create, change, delete tools
    assert not any(b.key == "cam_options_btn" for b in at.button)
    next(b for b in at.button if b.key == "cam_options_back").click().run()
    assert not at.exception and any(b.key == "cam_options_btn" for b in at.button)
    assert not any(b.key == "tools_new" for b in at.button)


def test_autocam_makes_the_operations_and_the_gcode_of_a_sheet(fake_db):
    import math
    from streamlit.testing.v1 import AppTest
    base = {"flutes": 3, "shoulder_diameter": 6.0, "arbor_diameter": 6.0, "shoulder_angle": 0.0, "cutting_length": 40.0, "shoulder_length": 50.0,
            "total_length": 50.0, "chamfer": 0.0, "outside_holder": 100.0, "helical_angle": 30.0, "holder": "", "point_angle": None}
    fake_db.list_cam_tools = lambda: [{**base, "number": 1, "tool_type": "END MILL", "name": "1/4 END MILL", "diameter": 6.35},
                                      {**base, "number": 4, "tool_type": "DRILL", "name": "DRILL 20", "diameter": 20.0}]
    ring = [[70.1367 + 10 * math.cos(2 * math.pi * i / 32), 22.3454 + 10 * math.sin(2 * math.pi * i / 32)] for i in range(32)]
    sheet = {"width": 150.0, "height": 80.0, "placements": [
        {"part": "Part2", "x": 0.0, "y": 0.0, "w": 100.0, "h": 50.0, "rotated": False,
         "polys": [{"ext": [[100.0, 0.0], [100.0, 50.0], [0.0, 50.0], [0.0, 0.0], [100.0, 0.0]], "holes": [ring + [ring[0]]]}]}]}
    fake_db.load_run = lambda i: {"id": i, "sheet_w": 150.0, "sheet_h": 80.0, "margin": 0.0, "spacing": 13.0, "seconds": 1.0, "result": [sheet]}
    at = AppTest.from_file(APP, default_timeout=60)
    at.session_state["pid"] = 1
    at.session_state["pending_settings"] = {"effort_label": "Quick"}
    at.run()
    assert any(b.key == "autocam_btn" for b in at.button)                            # the button is next to Create operation and CAM options
    next(b for b in at.button if b.label == "Load run").click().run()
    next(b for b in at.button if b.key == "autocam_btn").click().run()
    assert not at.exception and any(s.value == "AutoCam" for s in at.subheader)
    at.number_input(key="autocam_thickness").set_value(18.0)
    next(b for b in at.button if b.key == "autocam_run").click().run()
    assert not at.exception and not at.error
    names = [o["name"] for o in fake_db.list_operations(7)]
    assert names == ["Sheet1-DRILL-DRILL 20", "Sheet1-CONTOUR-1/4 END MILL"]                       # the round hole is drilled, the outline is milled
    op = {o["name"]: o["settings"] for o in fake_db.list_operations(7)}
    assert op["Sheet1-DRILL-DRILL 20"]["tool"] == 4 and op["Sheet1-DRILL-DRILL 20"]["drill"] == {"extra": 0.0, "peck": 0.0}
    assert op["Sheet1-CONTOUR-1/4 END MILL"]["tool"] == 1 and op["Sheet1-CONTOUR-1/4 END MILL"]["thickness"] == 18.0
    pic = at.session_state["autocam_result"]["picture"]                                 # the isometric picture above the G-code, with the tool numbers
    assert pic[:8] == b"\x89PNG\r\n\x1a\n" and len(pic) > 5000
    assert [t for _c, t in at.session_state["autocam_result"]["legend"]] == ["**T1** 1/4 END MILL Ø6.35", "**T4** DRILL 20 Ø20"]
    code = next(c.value for c in at.code)
    assert "G0 X70.137 Y22.345 Z45." in code and "G81 G98 Z0. R20 F150." in code and "G80" in code and code.count("M6 T") == 2
    assert "(Sheet1-DRILL-DRILL 20" in code and "(Sheet1-CONTOUR-1/4 END MILL" in code
    next(b for b in at.button if b.key == "autocam_run").click().run()               # again: the operations are replaced, not doubled
    assert [o["name"] for o in fake_db.list_operations(7)] == names
    next(b for b in at.button if b.key == "autocam_back").click().run()
    assert any(b.key == "autocam_btn" for b in at.button) and not any(s.value == "AutoCam" for s in at.subheader)
