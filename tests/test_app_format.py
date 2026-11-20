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
