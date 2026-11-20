"""True-shape 2D nesting for arbitrary polygons (with holes) on a raster grid.

Core (one layout = one "decode")
--------------------------------
* The sheet is a grid of square cells (`cell` units); occupied cells are 1.
* A part (rotated, inflated by half the spacing) is rasterised *conservatively*:
  every cell it touches is marked. Masks that share no cell can never overlap,
  so every result is overlap-free by construction (verified in the tests).
* For each rotation, ALL feasible positions are found at once with an FFT
  cross-correlation; a placement rule picks the best one. Parts fall into the
  pockets and holes of earlier parts automatically.
* Parts go largest first, first-fit over the sheets, a new sheet when needed.

Search (what makes it smart)
----------------------------
* Pairing: for parts with qty >= 2 the tightest "2-up" (the part plus a rotated
  copy, e.g. two right triangles hypotenuse to hypotenuse) is built, and pairs
  of pairs again; clusters that are clearly tighter than the single part are
  nested as rigid units.
* Placement rules: min length (x), min width (y), min bounding-box growth, and
  max contact with neighbours/edges - each packs differently.
* Multi-start: deterministic baselines first, then randomised restarts (part
  order, rule, noisy choice of rotation) until the time budget is spent. The best
  layout wins: fewest sheets, then the emptiest last sheet, then the shortest nest.
"""
from __future__ import annotations

import math
import random
import time
from dataclasses import dataclass, field

import numpy as np
import scipy.fft
from matplotlib.path import Path
from scipy.ndimage import binary_dilation
from scipy.signal import fftconvolve
from shapely import affinity

from nesting import Placement, Sheet
from shapes import geom_to_json

PAD_FACTOR = 0.75  # > sqrt(2)/2: any cell touched by the part has its centre inside the padded part
RULES = ("xend", "env", "contact", "yend")
ORDERS = ("area", "long", "perimeter", "width", "height")


@dataclass
class PolyPart:
    name: str
    geom: object  # shapely Polygon/MultiPolygon, bbox lower-left at (0, 0)
    qty: int = 1
    rotate: bool = True
    members: list | None = None  # rigid cluster: [(name, geom in cluster coordinates)]
    _cache: dict = field(default_factory=dict, repr=False)


@dataclass
class _Var:
    ang: float
    mask: np.ndarray
    off: tuple
    geom: object
    ring: np.ndarray | None = None


def _polys(g):
    return [g] if g.geom_type == "Polygon" else [x for x in g.geoms if x.geom_type == "Polygon"]


def _mask(geom, cell, pad):
    infl = geom.buffer(pad)
    minx, miny, maxx, maxy = infl.bounds
    shifted = affinity.translate(infl, -minx, -miny)
    w = int(math.ceil((maxx - minx) / cell)) + 1
    h = int(math.ceil((maxy - miny) / cell)) + 1
    xs = (np.arange(w) + 0.5) * cell
    ys = (np.arange(h) + 0.5) * cell
    X, Y = np.meshgrid(xs, ys)
    pts = np.column_stack([X.ravel(), Y.ravel()])
    m = np.zeros(len(pts), dtype=bool)
    for poly in _polys(shifted):
        inside = Path(np.asarray(poly.exterior.coords)).contains_points(pts)
        for hole in poly.interiors:
            inside &= ~Path(np.asarray(hole.coords)).contains_points(pts)
        m |= inside
    return m.reshape(h, w), (minx, miny)


def _variants(part: PolyPart, step: float, cell: float, pad: float):
    """Distinct rotations of a part (cached on the part)."""
    key = (step, cell, pad)
    if key in part._cache:
        return part._cache[key]
    angles = [0.0]
    if part.rotate and step and step < 360:
        angles = [a * step for a in range(int(round(360 / step)))]
    seen, out = set(), []
    c = part.geom.centroid
    for ang in angles:
        g = affinity.rotate(part.geom, ang, origin=(c.x, c.y)) if ang else part.geom
        m, off = _mask(g, cell, pad)
        k = (m.shape, np.packbits(m).tobytes())
        if k in seen:
            continue
        seen.add(k)
        out.append(_Var(ang, m, off, g))
    part._cache[key] = out
    return out


def _ring(v: _Var):
    """1-cell frame around the mask, (h+2, w+2); used for the contact rule."""
    if v.ring is None:
        p = np.pad(v.mask, 1)
        v.ring = (binary_dilation(p, structure=np.ones((3, 3), bool)) & ~p).astype(np.float32)
    return v.ring


class _Grid:
    def __init__(self, sheet_w, sheet_h, margin, spacing, cell):
        self.nx = int(math.ceil(sheet_w / cell))
        self.ny = int(math.ceil(sheet_h / cell))
        self.occ = np.ones((self.ny, self.nx), dtype=np.float32)
        lo, hi_x, hi_y = margin - spacing / 2, sheet_w - margin + spacing / 2, sheet_h - margin + spacing / 2
        x0, x1 = max(0, math.ceil(lo / cell)), min(self.nx, math.floor(hi_x / cell))
        y0, y1 = max(0, math.ceil(lo / cell)), min(self.ny, math.floor(hi_y / cell))
        if x1 > x0 and y1 > y0:
            self.occ[y0:y1, x0:x1] = 0.0
        self.occ_pad = None
        self.xc = self.yc = 0  # extent of the nest so far (cells)
        self.sheet = Sheet(sheet_w, sheet_h)

    def best(self, variants, rule="xend", rng=None, noise=0.0):
        """Best feasible (variant_index, ox, oy) on this sheet, or None."""
        if rule == "contact" and self.occ_pad is None:
            self.occ_pad = np.pad(self.occ, 1, constant_values=1.0)
        cands = []
        for k, v in enumerate(variants):
            h, w = v.mask.shape
            if h > self.ny or w > self.nx:
                continue
            ov = fftconvolve(self.occ, v.mask[::-1, ::-1].astype(np.float32), mode="valid")
            feas = ov < 0.5
            if not feas.any():
                continue
            xs = np.arange(feas.shape[1])[None, :]
            ys = np.arange(feas.shape[0])[:, None]
            if rule == "yend":
                score = (ys + h) * (self.nx + 1.0) + xs
            elif rule == "env":
                score = np.maximum(self.xc, xs + w) * np.maximum(self.yc, ys + h) + 1e-3 * (xs + ys)
            elif rule == "contact":
                ring = _ring(v)
                contact = fftconvolve(self.occ_pad, ring[::-1, ::-1], mode="valid")
                score = -contact + 1e-3 * (xs + w) / self.nx + 1e-6 * ys / self.ny
            else:  # xend
                score = (xs + w) * (self.ny + 1.0) + ys
            score = np.where(feas, score, np.inf)
            idx = np.unravel_index(np.argmin(score), score.shape)
            cands.append((float(score[idx]), k, int(idx[1]), int(idx[0])))
        if not cands:
            return None
        cands.sort()
        if rng is not None and noise and len(cands) > 1 and rng.random() < noise:
            return cands[1][1:]
        return cands[0][1:]

    def commit(self, m, ox, oy):
        h, w = m.shape
        self.occ[oy:oy + h, ox:ox + w][m] = 1.0
        self.occ_pad = None
        self.xc = max(self.xc, ox + w)
        self.yc = max(self.yc, oy + h)


# ------------------------------------------------------------------ pairing
def _best_pair(part: PolyPart, step, cell, spacing):
    """Tightest 2-up of a part. Returns (cluster for one pair, eff_pair, eff_single) or None."""
    gx0, gy0, gx1, gy1 = part.geom.bounds
    pcell = min(cell, max(cell / 3.0, max(gx1 - gx0, gy1 - gy0) / 400.0))  # finer than the sheet grid
    pad = spacing / 2 + PAD_FACTOR * pcell
    base = _variants(PolyPart(part.name, part.geom, 1, part.rotate), step, pcell, pad)
    a = base[0]
    (aminx, aminy) = a.off
    mb = max(max(v.mask.shape) for v in base)
    ha, wa = a.mask.shape
    gh, gw = ha + 2 * mb, wa + 2 * mb
    ax0 = ay0 = mb
    occ = np.zeros((gh, gw), dtype=np.float32)
    occ[ay0:ay0 + ha, ax0:ax0 + wa][a.mask] = 1.0
    best = None
    for v in base:
        h, w = v.mask.shape
        ov = fftconvolve(occ, v.mask[::-1, ::-1].astype(np.float32), mode="valid")
        feas = ov < 0.5
        if not feas.any():
            continue
        ys, xs = np.nonzero(feas)
        bw = np.maximum(ax0 + wa, xs + w) - np.minimum(ax0, xs)
        bh = np.maximum(ay0 + ha, ys + h) - np.minimum(ay0, ys)
        area = bw * bh
        i = int(np.argmin(area))
        if best is None or area[i] < best[0]:
            best = (area[i], xs[i], ys[i], v)
    if best is None:
        return None
    _, ox, oy, v = best
    c = part.geom.centroid
    ga = affinity.translate(a.geom, -aminx + ax0 * pcell, -aminy + ay0 * pcell)
    gb = affinity.translate(v.geom, -v.off[0] + ox * pcell, -v.off[1] + oy * pcell)
    x0 = min(ga.bounds[0], gb.bounds[0])
    y0 = min(ga.bounds[1], gb.bounds[1])
    ga, gb = affinity.translate(ga, -x0, -y0), affinity.translate(gb, -x0, -y0)
    union = ga.union(gb)
    # members of the new cluster: the part's own members placed the same way as ga / gb
    mem_a, mem_b = [], []
    for nm, mg in _flat_members(part):
        mem_a.append((nm, affinity.translate(mg, -aminx + ax0 * pcell - x0, -aminy + ay0 * pcell - y0)))
        rg = affinity.rotate(mg, v.ang, origin=(c.x, c.y)) if v.ang else mg
        mem_b.append((nm, affinity.translate(rg, -v.off[0] + ox * pcell - x0, -v.off[1] + oy * pcell - y0)))
    bx0, by0, bx1, by1 = union.bounds
    eff_pair = (ga.area + gb.area) / ((bx1 - bx0) * (by1 - by0))
    eff_single = part.geom.area / ((gx1 - gx0) * (gy1 - gy0))
    return PolyPart(part.name + " x2", union, 1, part.rotate, mem_a + mem_b), eff_pair, eff_single


def _flat_members(part):
    return part.members or [(part.name, part.geom)]


def _build_clusters(parts, step, cell, spacing):
    """Parts list where qty>=2 parts are replaced by rigid 2-up (and 4-up) clusters when clearly tighter."""
    out, any_cluster = [], False
    for p in parts:
        qty = int(p.qty)
        found = None
        if qty >= 2 and p.members is None:
            try:
                found = _best_pair(p, step, cell, spacing)
            except Exception:  # noqa: BLE001 - pairing is only an optimisation
                found = None
        if not (found and found[1] >= 1.15 * found[2]):
            out.append(p)
            continue
        pair, eff_pair, _ = found
        any_cluster = True
        n_pairs, odd = qty // 2, qty % 2
        quad = None
        if n_pairs >= 2:  # pair of pairs
            try:
                q = _best_pair(PolyPart(pair.name, pair.geom, n_pairs, pair.rotate, pair.members), step, cell, spacing)
            except Exception:  # noqa: BLE001
                q = None
            if q and q[1] >= 1.05 * eff_pair:
                quad = q[0]
        if quad is not None:
            quad.qty = n_pairs // 2
            out.append(quad)
            if n_pairs % 2:
                out.append(PolyPart(pair.name, pair.geom, 1, pair.rotate, pair.members))
        else:
            pair.qty = n_pairs
            out.append(pair)
        if odd:
            out.append(PolyPart(p.name, p.geom, 1, p.rotate))
    return out, any_cluster


# -------------------------------------------------------------------- decode
def _nest_once(jobs, sheet_w, sheet_h, margin, spacing, rot_step, cell, pad, rule, rng=None, noise=0.0):
    grids: list[_Grid] = []
    for part in jobs:
        variants = _variants(part, rot_step, cell, pad)
        c = part.geom.centroid
        members = _flat_members(part)
        for _ in range(int(part.qty)):
            placed = None
            for g in grids:
                placed = g.best(variants, rule, rng, noise)
                if placed:
                    grid = g
                    break
            if not placed:
                grid = _Grid(sheet_w, sheet_h, margin, spacing, cell)
                placed = grid.best(variants, rule, rng, noise)
                if not placed:
                    raise ValueError(f"Part '{part.name}' does not fit on the sheet")
                grids.append(grid)
            k, ox, oy = placed
            v = variants[k]
            grid.commit(v.mask, ox, oy)
            for name, mg in members:
                if part.members:
                    mg = affinity.rotate(mg, v.ang, origin=(c.x, c.y)) if v.ang else mg
                else:
                    mg = v.geom
                tg = affinity.translate(mg, -v.off[0] + ox * cell, -v.off[1] + oy * cell)
                x0, y0, x1, y1 = tg.bounds
                grid.sheet.placements.append(Placement(
                    name, x0, y0, x1 - x0, y1 - y0, v.ang % 360 != 0, float(v.ang),
                    float(tg.area), geom_to_json(tg)))
    return [g.sheet for g in grids]


def _fitness(sheets):
    last = sheets[-1]
    extent = max((p.x + p.w) for p in last.placements)
    return (len(sheets), round(last.used_area / (last.width * last.height), 3), extent)


def _order(parts, how, rng=None):
    keys = {
        "area": lambda p: -p.geom.area,
        "long": lambda p: -max(p.geom.bounds[2] - p.geom.bounds[0], p.geom.bounds[3] - p.geom.bounds[1]),
        "perimeter": lambda p: -p.geom.convex_hull.length,
        "width": lambda p: -(p.geom.bounds[2] - p.geom.bounds[0]),
        "height": lambda p: -(p.geom.bounds[3] - p.geom.bounds[1]),
    }
    if how == "shuffle":
        out = sorted(parts, key=lambda p: -p.geom.area * rng.uniform(0.6, 1.4))
    else:
        out = sorted(parts, key=keys[how])
    return out


def nest_polygons(parts, sheet_w, sheet_h, margin=0.0, spacing=0.0, rot_step=90.0, cell=None,
                  progress=None, time_limit=0.0, seed=1, info=None):
    """Nest PolyParts on identical sheets; returns list[Sheet].

    time_limit: seconds of extra randomised search after the deterministic baselines
    (0 = baselines only, fast). progress(fraction, message) is called as it goes.
    info (dict, optional) is filled with run statistics.
    """
    if sheet_w - 2 * margin <= 0 or sheet_h - 2 * margin <= 0:
        raise ValueError("Margin is too large for the sheet")
    cell = cell or max(sheet_w, sheet_h) / 800.0
    pad = spacing / 2 + PAD_FACTOR * cell
    rng = random.Random(seed)
    t0 = time.monotonic()

    clusters, any_cluster = _build_clusters(parts, rot_step, cell, spacing)
    sets = {"plain": list(parts)}
    if any_cluster:
        sets["paired"] = clusters

    plan = []  # deterministic baselines, most promising first
    for name in (["paired"] if any_cluster else []) + ["plain"]:
        plan.append((name, "xend", "area", 0.0))
    for name in (["paired"] if any_cluster else []) + ["plain"]:
        for rule in ("env", "contact", "yend"):
            plan.append((name, rule, "area", 0.0))
    n_base_min = len(sets)  # always run at least xend for each set

    best, best_fit, errors, runs = None, None, [], 0
    i = 0
    while True:
        elapsed = time.monotonic() - t0
        if i < len(plan):
            if i >= n_base_min and elapsed >= time_limit:
                break
            setname, rule, order, noise = plan[i]
        else:
            if elapsed >= time_limit:
                break
            setname = rng.choice(list(sets))
            rule = rng.choice(RULES)
            order = rng.choice(ORDERS + ("shuffle",))
            noise = rng.choice((0.0, 0.1, 0.25))
        i += 1
        jobs = _order(sets[setname], order, rng)
        try:
            res = _nest_once(jobs, sheet_w, sheet_h, margin, spacing, rot_step, cell, pad, rule,
                             rng if noise else None, noise)
        except ValueError as e:
            errors.append(e)
            if len(errors) > 3 + len(plan) and best is None:
                break
            continue
        runs += 1
        fit = _fitness(res)
        if best is None or fit < best_fit:
            best, best_fit = res, fit
            if info is not None:
                info.update(set=setname, rule=rule, order=order, noise=noise)
        if progress:
            frac = min(1.0, (time.monotonic() - t0) / max(time_limit, 1e-9)) if time_limit else min(1.0, i / n_base_min)
            progress(frac, f"layouts tried: {runs} · best: {best_fit[0]} sheet(s), "
                           f"last sheet {best_fit[1]:.0%} full")
    if best is None:
        raise errors[0]
    if info is not None:
        info.update(runs=runs, seconds=time.monotonic() - t0, fitness=best_fit)
    return best
