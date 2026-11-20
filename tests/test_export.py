import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
import io, zipfile
import ezdxf
import pytest
from shapely.geometry import Polygon, box

from dxf_export import all_sheets_dxf, sheet_dxf, sheets_zip
from dxf_io import read_dxf
from nesting import Part, nest
from nesting_poly import PolyPart, nest_polygons
from shapes import build_shape, geom_from_json


def layer_polys(data, layer):
    doc = ezdxf.read(io.StringIO(data.decode("utf-8")))
    return [e for e in doc.modelspace() if e.dxftype() == "LWPOLYLINE" and e.dxf.layer == layer]


def test_poly_sheet_roundtrip_with_holes():
    ring = box(0, 0, 200, 200).difference(box(50, 50, 150, 150))
    sheets = nest_polygons([PolyPart("frame", ring, 2), PolyPart("c", build_shape("circle", 60), 3)], 500, 450, 5, 3)
    data = sheet_dxf(sheets[0])
    cut = layer_polys(data, "CUT")
    n_rings = 0
    for p in sheets[0].placements:
        g = geom_from_json(p.polys)
        n_rings += 1 + len(g.interiors)  # outline + holes
    assert len(cut) == n_rings and all(e.closed for e in cut)
    assert len(layer_polys(data, "SHEET")) == 1
    # the contours re-import as parts with their holes
    parts = read_dxf(data, one_part=False)
    assert any(len(g.interiors) == 1 for g in parts)


def test_rectangle_engine_export_and_zip():
    sheets = nest([Part("a", 100, 50, 30)], 300, 200)
    assert len(sheets) >= 1
    z = zipfile.ZipFile(io.BytesIO(sheets_zip(sheets)))
    assert len(z.namelist()) == len(sheets) and z.namelist()[0].endswith(".dxf")
    first = layer_polys(z.read(z.namelist()[0]), "CUT")
    assert len(first) == len(sheets[0].placements)
    one = all_sheets_dxf(sheets)
    assert len(layer_polys(one, "SHEET")) == len(sheets)


def test_combined_file_stacks_sheets_vertically_with_titles():
    sheets = nest([Part("a", 100, 100, 30)], 300, 200)
    assert len(sheets) >= 3
    doc = ezdxf.read(io.StringIO(all_sheets_dxf(sheets).decode("utf-8")))
    msp = doc.modelspace()
    outlines = [e for e in msp if e.dxftype() == "LWPOLYLINE" and e.dxf.layer == "SHEET"]
    xs = {round(min(p[0] for p in e.get_points()), 3) for e in outlines}
    tops = [max(p[1] for p in e.get_points()) for e in outlines]
    assert xs == {0.0}                                       # all in one column
    assert tops == sorted(tops, reverse=True) and len(set(tops)) == len(tops)  # sheet 1 on top, going down
    titles = {e.dxf.text: e.dxf.insert for e in msp if e.dxftype() == "TEXT" and e.dxf.layer == "SHEET"}
    assert [f"Sheet {i}" for i in range(1, len(sheets) + 1)] == sorted(titles, key=lambda t: int(t.split()[1]))
    assert all(p[0] < 0 for p in titles.values())             # beside (left of) the sheets
    # each title is level with its own sheet
    for i, top in enumerate(tops, 1):
        y = titles[f"Sheet {i}"][1]
        assert top - 200 <= y <= top
