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
    pts = ((( 0.0, 0.0), (100.0, 0.0), (100.0, 50.0), (0.0, 0.0)),)
    a = isometric_png(1000.0, 600.0, 18.0, pts)
    assert a[:8] == b"\x89PNG\r\n\x1a\n" and plan_png(1000.0, 600.0, pts, ("Outline",))[:4] == b"\x89PNG"
    assert isometric_png(1000.0, 600.0, 18.0, pts) == a
