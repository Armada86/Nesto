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


def _label_sheet(n_small):
    from nesting import Placement, Sheet
    from shapely.geometry import box
    from shapes import geom_to_json

    def pl(name, g):
        b = g.bounds
        return Placement(name, b[0], b[1], b[2] - b[0], b[3] - b[1], False, 0.0, g.area, geom_to_json(g))
    parts = [pl("BIG", box(10, 10, 2400, 900))]
    parts += [pl(f"s{k}", box(20 + k * 90, 950, 20 + k * 90 + 22, 950 + 14)) for k in range(n_small)]
    parts += [pl("T-90", box(2450, 10, 2490, 700))]                              # tall and thin: the name is turned
    return Sheet(2500, 1250, parts)


def test_every_name_is_inside_its_part_and_small_parts_get_a_smaller_font():
    from render import draw_sheet
    import matplotlib.pyplot as plt
    sh = _label_sheet(5)
    fig = draw_sheet(sh, "Sheet 1")
    ax = fig.axes[0]
    renderer = fig.canvas.get_renderer()
    texts = {t.get_text(): t for t in ax.texts if not t.get_text().startswith("scrap")}
    assert set(texts) == {"BIG", "s0", "s1", "s2", "s3", "s4", "T-90"}                 # every part has its name
    assert texts["BIG"].get_fontsize() == 7.0                                          # big part: normal size
    for p in sh.placements:
        t = texts[p.part]
        bb = t.get_window_extent(renderer)
        (x0, y0), (x1, y1) = ax.transData.transform((p.x, p.y)), ax.transData.transform((p.x + p.w, p.y + p.h))
        assert bb.x0 >= x0 - 1 and bb.x1 <= x1 + 1 and bb.y0 >= y0 - 1 and bb.y1 <= y1 + 1, p.part     # inside the part
        if p.part.startswith("s"):
            assert t.get_fontsize() < 7.0                                              # small part: smaller font
    assert texts["T-90"].get_rotation() == 90                                          # a thin tall part: the name is turned
    assert ax.get_xlim()[1] < 2600                                                     # no column of labels beside the sheet
    plt.close(fig)
