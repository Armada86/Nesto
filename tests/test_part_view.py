import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
import matplotlib.pyplot as plt
import pytest
from shapely import affinity
from shapely.geometry import box
from shapely.ops import unary_union

from part_view import draw_part, fmt, hole_summary, part_stats
from shapes import build_shape, normalize


def texts(fig):
    return [t.get_text() for ax in fig.axes for t in ax.texts] + [t.get_text() for t in fig.texts]


def test_fmt_snaps_polygon_approximations():
    assert fmt(250.075) == "250" and fmt(12.0) == "12" and fmt(123.4) == "123.4" and fmt(0.75) == "0.75"


def test_rectangle_shows_overall_width_and_height_once():
    fig = draw_part(build_shape("rectangle", 100, 50), "r")
    t = texts(fig)
    assert "100" in t and "50" in t and t.count("100") == 1 and t.count("50") == 1   # no duplicate edge labels
    plt.close(fig)


def test_circle_shows_diameter():
    g = build_shape("circle", 60)
    s = part_stats(g)
    assert s["round"] and s["diameter"] == pytest.approx(60, abs=0.1)
    fig = draw_part(g)
    assert "Ø 60" in texts(fig)
    plt.close(fig)


def test_triangle_labels_slanted_edges():
    fig = draw_part(build_shape("triangle", 120, 90))
    t = texts(fig)
    assert "120" in t and "90" in t and t.count("108.2") == 2        # two equal slanted sides
    plt.close(fig)


def test_plate_with_holes_summary_and_labels():
    holes = unary_union([affinity.translate(build_shape("circle", 24), x, y) for x, y in ((30, 30), (246, 30), (30, 126), (246, 126))]
                        + [affinity.translate(build_shape("obround", 90, 30), 105, 75)])
    g = normalize(box(0, 0, 300, 180).difference(holes))
    s = part_stats(g)
    assert len(s["holes"]) == 5 and s["width"] == pytest.approx(300) and s["height"] == pytest.approx(180)
    assert hole_summary(s["holes"]) == ["4× Ø24", "90 × 30"]
    fig = draw_part(g)
    t = texts(fig)
    assert t.count("Ø24") == 4 and "90×30" in t and any("Holes: 4× Ø24" in x for x in t)
    plt.close(fig)


def test_many_holes_only_summary():
    holes = unary_union([affinity.translate(build_shape("circle", 6), 10 + 12 * i, 10 + 12 * j) for i in range(5) for j in range(4)])
    g = normalize(box(0, 0, 80, 70).difference(holes))
    fig = draw_part(g)
    t = texts(fig)
    assert not any(x == "Ø6" for x in t) and any("20× Ø6" in x for x in t)
    plt.close(fig)


def test_l_shape_and_multipiece_do_not_crash():
    l = normalize(unary_union([box(0, 0, 200, 40), box(0, 0, 40, 150)]))
    fig = draw_part(l); assert "200" in texts(fig) and "150" in texts(fig); plt.close(fig)
    two = unary_union([box(0, 0, 50, 50), box(100, 0, 150, 50)])
    fig = draw_part(two); plt.close(fig)


def test_every_part_is_drawn_in_the_same_box():
    """Tall/narrow, wide/flat, tiny and huge parts must all give a PNG of identical pixel size."""
    import io
    from PIL import Image
    from shapely.geometry import Polygon
    from part_view import PNG_SIZE, part_png
    tall = Polygon([(0, 0), (150, 0), (110, 1320), (40, 1320)])          # like the part in the bug report
    parts = [tall, box(0, 0, 2400, 12), box(0, 0, 3, 3), build_shape("circle", 60), build_shape("triangle", 5000, 4000)]
    sizes = {Image.open(io.BytesIO(part_png(g, "x"))).size for g in parts}
    assert sizes == {PNG_SIZE}


def test_tall_part_is_scaled_down_not_cropped():
    """The drawing of a very tall part keeps all its dimension labels inside the canvas."""
    from shapely.geometry import Polygon
    tall = Polygon([(0, 0), (150, 0), (110, 1320), (40, 1320)])
    fig = draw_part(tall)
    ax = fig.axes[0]
    x0, x1 = ax.get_xlim(); y0, y1 = ax.get_ylim()
    for t in ax.texts:
        x, y = t.get_position()
        assert x0 <= x <= x1 and y0 <= y <= y1
    plt.close(fig)
