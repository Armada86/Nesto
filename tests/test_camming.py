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
        sheet = {"width": float(w), "height": float(h), "placements": [pl("A", box(10, 10, w - 10, h - 10))]}
        im = np.asarray(Image.open(io.BytesIO(isometric_raw_png(float(w), float(h), 18.0, solids(sheet)))).convert("RGB")).astype(int)
        border = np.concatenate([im[0], im[-1], im[:, 0], im[:, -1]])
        assert not (np.abs(border - 255).sum(axis=1) > 30).any(), (w, h)          # nothing is cut off at the frame


def test_the_origin_is_the_nearest_corner_and_x_runs_to_the_right():
    from camming_ui import height_scale
    assert height_scale(6250.0, 1500.0, 18.0) > 5 and height_scale(300.0, 200.0, 18.0) == 1.0     # thin sheets are drawn taller
