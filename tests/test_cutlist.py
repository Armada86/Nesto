import io
import sys
import pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
import pytest
from openpyxl import load_workbook
from shapely.geometry import box

from cutlist_export import cutlist_xlsx
from nesting import Part, nest, summary
from nesting_poly import PolyPart, free_rectangles, nest_polygons
from shapes import build_shape


def rows(ws):
    return [[c.value for c in r] for r in ws.iter_rows()]


@pytest.fixture(scope="module")
def nested():
    parts = [PolyPart("plate", box(0, 0, 300, 100).difference(box(120, 30, 180, 70)), 3),
             PolyPart("disc", build_shape("circle", 60), 4), PolyPart("tri", build_shape("right triangle", 120, 80), 2)]
    sheets = nest_polygons(parts, 700, 400, 5, 3, 90, time_limit=0)
    listed = [{"name": "plate", "shape": "dxf", "width": 300.0, "height": 100.0, "qty": 3},
              {"name": "disc", "shape": "circle", "width": 60.0, "height": 60.0, "qty": 4},
              {"name": "tri", "shape": "right triangle", "width": 120.0, "height": 80.0, "qty": 2}]
    offcuts = [free_rectangles(s, 5) for s in sheets]
    return sheets, listed, offcuts


def test_workbook_has_the_four_tabs(nested):
    sheets, listed, offcuts = nested
    wb = load_workbook(io.BytesIO(cutlist_xlsx(sheets, "Job 7", margin=5, spacing=3, seconds=4.25, parts=listed, offcuts=offcuts)))
    assert wb.sheetnames == ["Summary", "Parts", "Placements", "Offcuts"]


def test_summary_matches_the_nest(nested):
    sheets, listed, offcuts = nested
    wb = load_workbook(io.BytesIO(cutlist_xlsx(sheets, "Job 7", margin=5, spacing=3, seconds=4.25, parts=listed, offcuts=offcuts)))
    ws = wb["Summary"]
    assert ws["A1"].value == "Cut list – Job 7"
    kv = {r[0]: r[1] for r in rows(ws) if r[0] and r[1] is not None}
    s = summary(sheets)
    assert kv["Sheets used"] == s["sheets"] and kv["Parts placed"] == s["parts"] == 9
    assert kv["Utilization"] == pytest.approx(s["utilization"])
    assert kv["Sheet length (x)"] == 700 and kv["Sheet width (y)"] == 400
    assert kv["Edge margin"] == 5 and kv["Part spacing (kerf)"] == 3
    assert kv["Nesting time (s)"] == pytest.approx(4.25, abs=0.06)
    util_cell = next(c for r in ws.iter_rows() for c in r if c.value == kv["Utilization"])
    assert util_cell.number_format == "0.00%"                                  # two decimals, as everywhere in the app


def test_placements_lists_every_placed_part_with_its_position(nested):
    sheets, listed, offcuts = nested
    wb = load_workbook(io.BytesIO(cutlist_xlsx(sheets, "J", parts=listed, offcuts=offcuts)))
    data = rows(wb["Placements"])
    assert data[0][:9] == ["Sheet", "Part", "X", "Y", "Box length (x)", "Box width (y)", "Rotation (°)", "Flipped", "Area"]
    body = [r for r in data[1:] if r[0] is not None]
    assert len(body) == sum(len(s.placements) for s in sheets)
    expected = sorted((i, p.part, round(p.x, 3), round(p.y, 3)) for i, s in enumerate(sheets, 1) for p in s.placements)
    got = sorted((r[0], r[1], round(r[2], 3), round(r[3], 3)) for r in body)
    assert got == expected
    assert all(r[7] in ("yes", "no") for r in body)


def test_parts_tab_requested_vs_placed_and_warning_colour(nested):
    sheets, listed, offcuts = nested
    wb = load_workbook(io.BytesIO(cutlist_xlsx(sheets, "J", parts=listed, offcuts=offcuts)))
    ws = wb["Parts"]
    data = {r[0]: r for r in rows(ws) if r[0] and r[0] != "Part"}
    assert data["plate"][4] == 3 and data["plate"][5] == 3 and data["disc"][5] == 4 and data["tri"][5] == 2
    assert data["Total"][5] == 9 and data["Total"][4] == 9                     # plain numbers, not formulas
    assert all("sheet" not in str(r[8]).lower() for r in data.values() if r[8])  # "1 × 3, 2 × 1" style
    # ask for more than was placed: the cells turn red
    greedy = [dict(p, qty=p["qty"] + (5 if p["name"] == "disc" else 0)) for p in listed]
    ws2 = load_workbook(io.BytesIO(cutlist_xlsx(sheets, "J", parts=greedy, offcuts=offcuts)))["Parts"]
    disc_row = next(r for r in ws2.iter_rows(min_row=2) if r[0].value == "disc")
    assert disc_row[4].value == 9 and disc_row[5].value == 4
    assert disc_row[4].fill.fgColor.rgb.endswith("F8CBAD") and disc_row[5].fill.fgColor.rgb.endswith("F8CBAD")
    plate_row = next(r for r in ws2.iter_rows(min_row=2) if r[0].value == "plate")
    assert plate_row[4].fill.fgColor.rgb in ("00000000", None) or not plate_row[4].fill.fgColor.rgb.endswith("F8CBAD")


def test_offcuts_tab(nested):
    sheets, listed, offcuts = nested
    ws = load_workbook(io.BytesIO(cutlist_xlsx(sheets, "J", parts=listed, offcuts=offcuts)))["Offcuts"]
    data = rows(ws)
    assert data[0] == ["Sheet", "X", "Y", "Length (x)", "Width (y)", "Area", "Squareness"]
    n = sum(len(o) for o in offcuts)
    assert (len(data) - 1 == n) if n else ("No usable" in str(data[1][0]))


def test_works_for_the_rectangle_engine_and_without_part_list():
    sheets = nest([Part("a", 100, 50, 12), Part("b", 80, 80, 5)], 400, 300, 5, 3)
    wb = load_workbook(io.BytesIO(cutlist_xlsx(sheets, "")))          # no project name, no part list, no offcuts
    assert wb["Summary"]["A1"].value == "Cut list"
    body = [r for r in rows(wb["Placements"])[1:] if r[0] is not None]
    assert len(body) == 17 and all(r[2] is not None for r in body)
    parts = {r[0]: r for r in rows(wb["Parts"]) if r[0] not in (None, "Part", "Total")}
    assert parts["a"][5] == 12 and parts["a"][4] is None              # placed known, requested unknown
    assert "No usable" in str(rows(wb["Offcuts"])[1][0])
