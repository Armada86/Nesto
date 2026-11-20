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
