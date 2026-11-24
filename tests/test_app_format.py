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
    db.save_settings = lambda *a: None
    db.save_run = lambda *a: None
    db.list_runs = lambda i: list(runs)
    db.load_run = lambda i: None
    db.delete_runs = lambda *a: 0
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
