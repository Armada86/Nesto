import pathlib
import sys
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
from shapely.geometry import box

from layout_edit import ensure_polys
from shapes import geom_to_json
import sheet_editor as se

COMPONENT = pathlib.Path(__file__).parent.parent / "components" / "sheet_editor"


def test_component_files_are_in_the_repository_and_linked():
    html = (COMPONENT / "index.html").read_text()
    assert (COMPONENT / "geom.js").exists() and '<script src="geom.js">' in html
    assert "streamlit:componentReady" in html and "streamlit:setComponentValue" in html and "streamlit:setFrameHeight" in html


def sheet(*names):
    pl = []
    for k, n in enumerate(names):
        g = box(10 + 120 * k, 10, 110 + 120 * k, 60)
        pl.append({"part": n, "x": 10 + 120 * k, "y": 10, "w": 100, "h": 50, "polys": geom_to_json(g)})
    return {"width": 1000.0, "height": 500.0, "placements": pl}


def test_same_part_name_gets_the_same_colour_and_tab20_is_used():
    p = se.palette(["a", "b", "a", "c"])
    assert p["a"] == "#1f77b4" and len({p["a"], p["b"], p["c"]}) == 3          # matplotlib tab20, first colours
    payload = se.parts_payload(sheet("a", "b", "a"))
    assert payload[0]["color"] == payload[2]["color"] != payload[1]["color"]
    assert [x["name"] for x in payload] == ["a", "b", "a"]


def test_rectangle_placements_without_outline_get_one_for_the_editor():
    sh = {"width": 500.0, "height": 300.0, "placements": [{"part": "r", "x": 5.0, "y": 5.0, "w": 100.0, "h": 50.0}]}
    payload = se.parts_payload(sh)                                              # works without stored outline
    ext = payload[0]["polys"][0]["ext"]
    assert (min(p[0] for p in ext), min(p[1] for p in ext), max(p[0] for p in ext), max(p[1] for p in ext)) == (5.0, 5.0, 105.0, 55.0)
    ensure_polys(sh)
    assert sh["placements"][0]["polys"] == payload[0]["polys"]
