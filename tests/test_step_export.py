"""STEP export of nested sheets: names, outlines, holes, stacking, zip."""
import io
import pathlib
import sys
import zipfile
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
import pytest

pytest.importorskip("OCP")
from shapely.geometry import box

import nesting_poly as npl
import step_export as se
import step_io
from nesting_poly import PolyPart


@pytest.fixture(scope="module")
def sheets():
    parts = [PolyPart("bar", box(0, 0, 500, 300).difference(box(100, 100, 200, 200)), 2), PolyPart("blk", box(0, 0, 400, 250), 2)]
    return npl.nest_polygons(parts, 1000, 700, 5, 3, rot_step=90, cell=4.0)


def test_one_sheet_keeps_names_outlines_and_holes(sheets):
    data = se.sheet_step(sheets[0], 1, thickness=3.0)
    text = data.decode("latin1")
    assert data.startswith(b"ISO-10303-21") and "bar" in text and "blk" in text and "Sheet 1" in text
    found = step_io.read_step(data)                                   # solid plates read back as parts
    areas = sorted(round(g.area) for g, _ in found)
    assert 140000 - 10 <= areas[-1] <= 140000 + 10 or 100000 in areas   # plate with a 100x100 hole (150000 - 10000) / plain 400x250
    assert sum(1 for g, _ in found if len(g.interiors) == 1) == sum(1 for s in sheets[:1] for p in s.placements if p.part == "bar")


def test_flat_faces_when_no_thickness(sheets):
    data = se.sheet_step(sheets[0], 1, thickness=0.0).decode("latin1")
    assert "ADVANCED_FACE" in data and data.count("MANIFOLD_SOLID_BREP") == 1      # only the sheet plate is a solid


def test_stacked_file_and_zip(sheets):
    two = list(sheets)[:1] * 2                                        # two sheets
    stacked = se.all_sheets_step(two)
    assert "Sheet 1" in stacked.decode("latin1") and "Sheet 2" in stacked.decode("latin1")
    names = zipfile.ZipFile(io.BytesIO(se.sheets_step_zip(two))).namelist()
    assert names == ["sheet_01.step", "sheet_02.step"]


def test_sheet_is_a_half_mm_plate_under_the_parts(sheets):
    import re
    sh = sheets[0]
    n_parts = len(sh.placements)
    pt_re = r"CARTESIAN_POINT\('',\(([-\d.E+]+),([-\d.E+]+),([-\d.E+]+)\)\)"
    plate = se.sheet_step(sh, 1, thickness=3.0).decode("latin1")
    assert plate.count("MANIFOLD_SOLID_BREP") == n_parts + 1             # the parts and the sheet plate
    pts = {(round(float(x), 3), round(float(y), 3), round(float(z), 3)) for x, y, z in re.findall(pt_re, plate)}
    assert {(0.0, 0.0, 0.0), (1000.0, 700.0, 0.0), (0.0, 0.0, -0.5), (1000.0, 700.0, -0.5)} <= pts   # 1000 x 700 x 0.5 mm, z -0.5 .. 0
    assert max(z for _, _, z in pts) == 3.0                              # part thickness as typed
    flat = se.sheet_step(sh, 1, thickness=0.0).decode("latin1")          # 0 in the box: the parts are 2D faces on z = 0
    assert flat.count("MANIFOLD_SOLID_BREP") == 1 and flat.count("ADVANCED_FACE") >= n_parts
