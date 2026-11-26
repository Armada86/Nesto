"""Camming step 1 - Geometry: the bottom contours of the parts."""
import pathlib
import sys
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
from shapely.geometry import Point, box

from camming_ui import chains, isometric_png, plan_png
from shapes import geom_to_json


def pl(name, g):
    b = g.bounds
    return {"part": name, "x": b[0], "y": b[1], "w": b[2] - b[0], "h": b[3] - b[1], "polys": geom_to_json(g)}


def test_chains_are_the_outline_and_every_hole_of_each_part_on_the_sheet_only():
    sheet = {"width": 1000.0, "height": 600.0, "placements": [
        pl("A", box(10, 10, 300, 200).difference(box(100, 60, 200, 150)).difference(Point(250, 40).buffer(10))),   # outline + 2 holes
        pl("B", box(310, 10, 600, 200)),
        pl("C", box(10, -500, 100, -420))]}                                                                          # in the hold area
    ch = chains(sheet)
    assert [(c["part"], c["type"]) for c in ch] == [("A", "Outline"), ("A", "Hole"), ("A", "Hole"), ("B", "Outline")]
    assert ch[0]["length"] == 2 * (290 + 190) and ch[1]["length"] == 2 * (100 + 90)
    for c in ch:
        assert c["pts"][0] == c["pts"][-1]                                       # closed chains


def test_pictures_are_made_and_cached():
    from camming_ui import solids
    sheet = {"width": 1000.0, "height": 600.0, "placements": [pl("A", box(10, 10, 300, 200).difference(box(100, 60, 200, 150)))]}
    a = isometric_png(1000.0, 600.0, 18.0, solids(sheet))
    pts = ((((0.0, 0.0), (100.0, 0.0), (100.0, 50.0), (0.0, 0.0))),)
    assert a[:8] == b"\x89PNG\r\n\x1a\n" and plan_png(1000.0, 600.0, pts, ("Outline",))[:4] == b"\x89PNG"
    assert isometric_png(1000.0, 600.0, 18.0, solids(sheet)) == a


def test_isometric_picture_shows_the_whole_sheet_for_any_proportion():
    import io
    import numpy as np
    from PIL import Image
    from camming_ui import isometric_raw_png, solids
    for w, h in ((6250, 1500), (3500, 1250), (1500, 1500), (2500, 600), (1200, 2400)):
        for k in (1.0, 12.0):
            sheet = {"width": float(w), "height": float(h), "placements": [pl("A", box(10, 10, w - 10, h - 10))]}
            im = np.asarray(Image.open(io.BytesIO(isometric_raw_png(float(w), float(h), 15.0, solids(sheet), k))).convert("RGB")).astype(int)
            border = np.concatenate([im[0], im[-1], im[:, 0], im[:, -1]])
            assert not (np.abs(border - 255).sum(axis=1) > 30).any(), (w, h, k)       # nothing, text included, is cut off at the frame


def test_the_picture_is_drawn_to_scale_unless_the_height_is_stretched():
    import io
    from PIL import Image
    from camming_ui import isometric_png, solids
    sheet = {"width": 3000.0, "height": 1000.0, "placements": [pl("A", box(10, 10, 2990, 990))]}
    flat = Image.open(io.BytesIO(isometric_png(3000.0, 1000.0, 15.0, solids(sheet), 1.0)))
    tall = Image.open(io.BytesIO(isometric_png(3000.0, 1000.0, 15.0, solids(sheet), 30.0)))
    assert tall.size[1] > flat.size[1] or tall.size != flat.size                 # stretching the height changes the picture
    assert isometric_png(3000.0, 1000.0, 15.0, solids(sheet), 1.0) == isometric_png(3000.0, 1000.0, 15.0, solids(sheet), 1.0)


def test_cached_pictures_depend_on_the_picture_version():
    """The pictures are cached; a new version of the drawing code must not be answered with an older cached picture."""
    import inspect
    import camming_ui
    for fn in (camming_ui.isometric_png, camming_ui.plan_png):
        assert "version" in inspect.signature(fn).parameters


def test_tool_validation():
    import tools_ui as tu
    ok = tu.blank(1)
    assert tu.validate(ok, set()) == []
    assert tu.validate(ok, {1})                                           # number already used
    assert tu.validate({**ok, "diameter": 0}, set())
    assert tu.validate({**ok, "cutting_length": 50, "shoulder_length": 40}, set())   # CL longer than SL
    assert tu.validate({**ok, "shoulder_length": 60, "total_length": 40}, set())     # SL longer than TL
    assert tu.validate({**ok, "outside_holder": 30}, set())                          # OHL shorter than TL
    assert tu.validate({**ok, "chamfer": 7}, set())                                  # chamfer more than half the diameter


def _tools_app(saved, cam):
    """An AppTest of the five steps of ONE operation, on a fake db. cam["v"] = {"camming": settings of the operation}."""
    import sys
    import types
    db = types.ModuleType("db")
    db.list_cam_tools = lambda: sorted(saved, key=lambda t: t["number"])

    def save(t, old=None):
        saved[:] = [x for x in saved if x["number"] != old]
        saved.append(t)
    db.save_cam_tool = save
    db.delete_cam_tool = lambda n: saved.__setitem__(slice(None), [x for x in saved if x["number"] != n])
    db.get_operation = lambda i: {"id": i, "run_id": 1, "name": "op", "settings": (cam.get("v") or {}).get("camming", {})}
    db.save_operation_settings = lambda i, s: cam.__setitem__("v", {"camming": s})
    sys.modules["db"] = db

    def app():
        from operations_ui import operation_view
        import db
        operation_view(db, {"id": 1, "name": "op", "settings": {}}, None)
    from streamlit.testing.v1 import AppTest
    return AppTest.from_function(app)


def _op():
    import sys
    from operations_ui import OpDB
    return OpDB(sys.modules["db"], 1)


def test_tools_tab_list_new_save_choose_and_delete():
    import sys
    saved, cam = [], {}
    try:
        at = _tools_app(saved, cam).run()
        assert not at.exception
        assert [t.label for t in at.tabs][:2] == ["1 · Geometry", "2 · Tools"]
        assert not [b for b in at.button if b.key == "tools_save"]            # nothing is open before a tool is chosen
        next(b for b in at.button if b.key == "tools_new").click().run()
        next(b for b in at.button if b.key == "tools_save").click().run()
        assert not at.exception and len(saved) == 1 and saved[0]["number"] == 1 and saved[0]["diameter"] == 12.0
        next(b for b in at.button if b.key == "tools_use").click().run()      # choose it for the CAM of the project
        assert cam["v"]["camming"]["tool"] == 1
        assert at.number_input(key="tl_diameter").disabled                      # a saved tool is read-only
        assert next(b for b in at.button if b.key == "tools_save").disabled
        next(b for b in at.button if b.key == "tools_edit_btn").click().run()   # Edit unlocks it
        assert not at.number_input(key="tl_diameter").disabled
        at.number_input(key="tl_diameter").set_value(9.0)
        next(b for b in at.button if b.key == "tools_save").click().run()
        assert saved[0]["diameter"] == 9.0 and at.number_input(key="tl_diameter").disabled
        import tools_ui
        assert tools_ui.chosen_tool(_op(), 1)["number"] == 1
        next(b for b in at.button if b.key == "tools_ask_delete").click().run()
        assert saved                                                           # asked first, nothing deleted yet
        next(b for b in at.button if b.key == "tools_delete").click().run()   # delete it completely
        assert saved == [] and cam["v"]["camming"]["tool"] is None
        assert tools_ui.chosen_tool(_op(), 1) is None
    finally:
        sys.modules.pop("db", None)


def test_data_validation_and_defaults():
    import data_ui as du
    ok = dict(du.DEFAULTS)
    assert du.validate(ok) == []
    assert du.validate({**ok, "feed_xy": 0}) and du.validate({**ok, "rpm": 20000}) and du.validate({**ok, "direction": "x"})


def test_data_tab_saves_with_the_project_and_reads_back():
    import sys
    saved, cam = [], {}
    try:
        at = _tools_app(saved, cam).run()
        assert [t.label for t in at.tabs][:3] == ["1 · Geometry", "2 · Tools", "3 · Data"]
        at.number_input(key="dt_feed_xy").set_value(1800.0)
        at.radio(key="dt_direction").set_value("CCW")
        next(b for b in at.button if b.key == "data_save").click().run()
        assert not at.exception
        d = cam["v"]["camming"]["data"]
        assert d["feed_xy"] == 1800.0 and d["direction"] == "CCW" and d["rpm"] == 10000.0 and "lead_in" not in d
        import data_ui
        assert data_ui.get_data(_op(), 1)["feed_xy"] == 1800.0
        at.number_input(key="dt_rpm").set_value(15000.0)
        next(b for b in at.button if b.key == "data_save").click().run()          # a good value, saved
        assert cam["v"]["camming"]["data"]["rpm"] == 15000.0
    finally:
        sys.modules.pop("db", None)


def test_levels_validation_and_defaults():
    import levels_ui as lu
    ok = {"clearance": 45.0, "safety": 2.0, "upper": 15.0, "depth": 15.0}
    assert lu.validate(ok) == []
    assert lu.validate({**ok, "clearance": 16.0})                     # not above upper + safety
    assert lu.validate({**ok, "depth": 0}) and lu.validate({**ok, "safety": -1})


def test_levels_tab_follows_the_geometry_thickness_until_changed():
    import sys
    saved, cam = [], {}
    try:
        at = _tools_app(saved, cam).run()
        assert [t.label for t in at.tabs][:4] == ["1 · Geometry", "2 · Tools", "3 · Data", "4 · Levels"]
        assert at.number_input(key="lv_upper").value == 18.0 and at.number_input(key="lv_depth").value == 18.0
        at.number_input(key="camming_thickness").set_value(15.0).run()     # the thickness of the Geometry step ...
        assert at.number_input(key="lv_upper").value == 15.0               # ... is the default upper level
        assert at.number_input(key="lv_depth").value == 15.0               # ... and the default profile depth
        assert not at.number_input(key="lv_upper").disabled                # the upper level can be changed here
        next(b for b in at.button if b.key == "levels_save").click().run()
        assert cam["v"]["camming"]["levels"] == {"clearance": 45.0, "safety": 2.0, "upper": None, "depth": None}   # still the defaults
        at.number_input(key="lv_upper").set_value(12.0)
        at.number_input(key="lv_depth").set_value(10.0)
        next(b for b in at.button if b.key == "levels_save").click().run()
        lv = cam["v"]["camming"]["levels"]
        assert lv["upper"] == 12.0 and lv["depth"] == 10.0
        import levels_ui
        assert levels_ui.get_levels(_op(), 1, 15.0) == {"clearance": 45.0, "safety": 2.0, "upper": 12.0, "depth": 10.0}
        at.number_input(key="lv_clearance").set_value(10.0)                # too low: refused
        next(b for b in at.button if b.key == "levels_save").click().run()
        assert cam["v"]["camming"]["levels"]["clearance"] == 45.0 and at.error
    finally:
        sys.modules.pop("db", None)
