"""The drag-and-drop collision code (components/sheet_editor/geom.js) is checked against Shapely."""
import json
import math
import random
import shutil
import subprocess
import sys
import pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
import pytest
from shapely import affinity
from shapely.geometry import Polygon, box

from shapes import build_shape, geom_to_json, normalize

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
RUNNER = str(pathlib.Path(__file__).parent / "js" / "run_geom.js")


def run_node(payload):
    r = subprocess.run(["node", RUNNER], input=json.dumps(payload), capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def star(rng, size):
    n = rng.randint(5, 10)
    angs = sorted(rng.uniform(0, 2 * math.pi) for _ in range(n))
    g = Polygon([(size * rng.uniform(0.4, 1) * math.cos(a), size * rng.uniform(0.4, 1) * math.sin(a)) for a in angs])
    return normalize(g if g.is_valid else g.buffer(0))


def random_shape(rng):
    kind = rng.choice(["star", "box", "circle", "frame", "tri"])
    s = rng.uniform(20, 90)
    if kind == "star":
        return star(rng, s)
    if kind == "box":
        return box(0, 0, s, s * rng.uniform(0.3, 1.5))
    if kind == "circle":
        return build_shape("circle", s)
    if kind == "frame":
        return box(0, 0, s * 2, s * 1.6).difference(box(s * 0.4, s * 0.4, s * 1.6, s * 1.2))
    return Polygon([(0, 0), (s, 0), (s * 0.3, s * 0.9)])


def test_js_collision_agrees_with_shapely_on_random_pairs():
    rng = random.Random(42)
    cases, truth = [], []
    while len(cases) < 500:
        a, b = random_shape(rng), random_shape(rng)
        # a third of the cases: put b inside the hole of a frame, or on top of a, or apart
        mode = rng.random()
        if mode < 0.2:
            frame = box(0, 0, 200, 160).difference(box(50, 40, 150, 120))
            a, b = frame, affinity.scale(star(rng, 25), rng.uniform(0.5, 1.6), 1, origin=(0, 0))
            bx, by = rng.uniform(30, 120), rng.uniform(30, 90)
        elif mode < 0.6:
            bx, by = rng.uniform(-60, 80), rng.uniform(-60, 80)        # near / overlapping
        else:
            bx, by = rng.uniform(-200, 250), rng.uniform(-200, 250)
        gap = rng.choice([0.2, 3.0])
        d = a.distance(affinity.translate(b, bx, by))
        if abs(d - gap) < 1e-6:
            continue
        cases.append({"a": geom_to_json(a, 6), "b": geom_to_json(b, 6), "ax": 0, "ay": 0, "bx": bx, "by": by, "gap": gap})
        truth.append(d < gap)
    out = run_node({"pairs": cases, "moves": []})["pairs"]
    wrong = [i for i, (o, t) in enumerate(zip(out, truth)) if o != t]
    assert not wrong, f"{len(wrong)} of {len(cases)} pairs disagree, e.g. case {wrong[0]}"
    assert 0.2 < sum(truth) / len(truth) < 0.9                      # the sample really mixes colliding and free pairs


def test_a_part_inside_the_hole_of_another_is_free_but_inside_its_material_collides():
    frame = box(0, 0, 100, 100).difference(box(30, 30, 70, 70))
    small = box(0, 0, 10, 10)
    cases = [{"a": geom_to_json(frame), "b": geom_to_json(small), "ax": 0, "ay": 0, "bx": 45, "by": 45, "gap": 3},   # in the hole
             {"a": geom_to_json(frame), "b": geom_to_json(small), "ax": 0, "ay": 0, "bx": 10, "by": 10, "gap": 3},   # in the material
             {"a": geom_to_json(frame), "b": geom_to_json(small), "ax": 0, "ay": 0, "bx": 66, "by": 45, "gap": 3}]   # across the hole edge
    assert run_node({"pairs": cases, "moves": []})["pairs"] == [False, True, True]


def test_moving_stops_at_contact_and_never_overlaps():
    rng = random.Random(7)
    W, H, margin, gap = 1000, 500, 5, 3.0
    moves, scenes = [], []
    for _ in range(60):
        parts, boxes = [], []
        # parts laid out on a loose grid so that none of them overlaps another
        for r in range(2):
            for c in range(5):
                g = random_shape(rng)
                g = affinity.scale(g, 0.9 * 150 / max(g.bounds[2], g.bounds[3], 1), 0.9 * 150 / max(g.bounds[2], g.bounds[3], 1), origin=(0, 0)) if max(g.bounds[2], g.bounds[3]) > 150 else g
                parts.append(affinity.translate(g, 10 + c * 195, 10 + r * 230))
        i = rng.randrange(len(parts))
        dx, dy = rng.uniform(-400, 400), rng.uniform(-250, 250)
        moves.append({"parts": [geom_to_json(p, 6) for p in parts], "i": i, "dx": dx, "dy": dy, "W": W, "H": H, "margin": margin, "gap": gap})
        scenes.append((parts, i, dx, dy))
    res = run_node({"pairs": [], "moves": moves})["moves"]
    stopped_short = 0
    for (parts, i, dx, dy), (x, y, blocked) in zip(scenes, res):
        moved = affinity.translate(parts[i], x, y)
        assert box(margin - 0.02, margin - 0.02, W - margin + 0.02, H - margin + 0.02).contains(moved)     # inside the sheet
        for k, o in enumerate(parts):
            if k != i:
                assert moved.distance(o) >= gap - 0.02, "the part overlaps / touches a neighbour"
        if not blocked:
            assert (x, y) == pytest.approx((dx, dy), abs=1e-6)                                             # free path: arrives
        elif math.hypot(x - dx, y - dy) > 1e-6:
            stopped_short += 1                                                                             # could not reach the target
    assert stopped_short > 10                                                                              # the scenes really test blocking


def test_a_blocked_part_slides_along_the_obstacle():
    """Pushing diagonally into a wall: the part must slide along it (x keeps moving), not stick."""
    wall = box(100, 0, 120, 400)                                       # a tall wall
    mover = box(10, 100, 40, 130)
    payload = {"pairs": [], "moves": [{"parts": [geom_to_json(mover), geom_to_json(wall)], "i": 0, "dx": 200, "dy": 100,
                                       "W": 1000, "H": 500, "margin": 5, "gap": 3}]}
    x, y, blocked = run_node(payload)["moves"][0]
    assert blocked
    assert x == pytest.approx(100 - 3 - 40, abs=0.1)                  # its right edge stopped 3 mm before the wall (x offset 57)
    assert y == pytest.approx(100, abs=0.1)                           # and it slid up along the wall as far as asked
