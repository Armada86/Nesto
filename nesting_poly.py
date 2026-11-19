"""True-shape 2D nesting for arbitrary polygons (with holes) on a raster grid.

How it works
------------
* The sheet is a grid of square cells (`cell` units). Occupied cells are 1.
* A part (rotated, inflated by half the spacing) is rasterised *conservatively*:
  every cell it touches is marked. Two masks that do not share a cell can never
  overlap, so the result is overlap-free by construction (verified in the tests).
* For each rotation, all feasible positions are found at once with an FFT
  cross-correlation of the sheet grid against the part mask; the position that
  minimises the used length (then height) wins. Parts go into pockets and holes
  of earlier parts automatically.
* Parts are placed largest first, first-fit over the sheets, new sheet when needed.
* Pairing: for parts with qty >= 2 the best "2-up" (the part plus a rotated copy,
  e.g. two right triangles hypotenuse to hypotenuse) is searched; if it is much
  tighter than the single part, pairs are nested as rigid units. Both strategies
  run and the one with fewer sheets wins.

Precision is limited by the cell size: parts end up roughly 1-2 cells further
apart than the requested spacing. Use a finer cell for a tighter result.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from matplotlib.path import Path
from scipy.signal import fftconvolve
from shapely import affinity

from nesting import Placement, Sheet
from shapes import geom_to_json

PAD_FACTOR = 0.75  # > sqrt(2)/2: any cell touched by the part has its centre inside the padded part


@dataclass
class PolyPart:
    name: str
    geom: object  # shapely Polygon/MultiPolygon, bbox lower-left at (0, 0)
    qty: int = 1
    rotate: bool = True
    members: list | None = None  # rigid cluster: [(name, geom in cluster coordinates)]


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
    """Distinct rotations of a part: list of (angle, mask, (minx, miny), rotated_geom)."""
    angles = [0.0]
    if part.rotate and step and step < 360:
        angles = [a * step for a in range(int(round(360 / step)))]
    seen, out = set(), []
    c = part.geom.centroid
    for ang in angles:
        g = affinity.rotate(part.geom, ang, origin=(c.x, c.y)) if ang else part.geom
        m, off = _mask(g, cell, pad)
        key = (m.shape, np.packbits(m).tobytes())
        if key in seen:
            continue
        seen.add(key)
        out.append((ang, m, off, g))
    return out


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
        self.sheet = Sheet(sheet_w, sheet_h)

    def best(self, variants):
        """Best feasible (variant_index, ox, oy) on this sheet or None."""
        best = None
        for k, (_, m, _, _) in enumerate(variants):
            h, w = m.shape
            if h > self.ny or w > self.nx:
                continue
            ov = fftconvolve(self.occ, m[::-1, ::-1].astype(np.float32), mode="valid")
            feas = ov < 0.5
            if not feas.any():
                continue
            xs = np.arange(feas.shape[1])
            ys = np.arange(feas.shape[0])
            score = (xs[None, :] + w) * (self.ny + 1.0) + ys[:, None]
            score = np.where(feas, score, np.inf)
            idx = np.unravel_index(np.argmin(score), score.shape)
            s = score[idx]
            if best is None or s < best[0]:
                best = (s, k, int(idx[1]), int(idx[0]))
        return None if best is None else best[1:]

    def commit(self, m, ox, oy):
        h, w = m.shape
        self.occ[oy:oy + h, ox:ox + w][m] = 1.0


def _best_pair(part: PolyPart, step, cell, pad):
    """Tightest 2-up of a part: returns (cluster PolyPart for ONE pair, efficiency) or None."""
    base = _variants(part, step, cell, pad)
    a_ang, a_mask, (aminx, aminy), a_geom = base[0]
    mb = max(max(m.shape) for _, m, _, _ in base)
    ha, wa = a_mask.shape
    gh, gw = ha + 2 * mb, wa + 2 * mb
    ax0, ay0 = mb, mb
    occ = np.zeros((gh, gw), dtype=np.float32)
    occ[ay0:ay0 + ha, ax0:ax0 + wa][a_mask] = 1.0
    best = None
    for _, m, off, g in base:
        h, w = m.shape
        ov = fftconvolve(occ, m[::-1, ::-1].astype(np.float32), mode="valid")
        feas = ov < 0.5
        if not feas.any():
            continue
        ys, xs = np.nonzero(feas)
        bw = np.maximum(ax0 + wa, xs + w) - np.minimum(ax0, xs)
        bh = np.maximum(ay0 + ha, ys + h) - np.minimum(ay0, ys)
        area = bw * bh
        i = int(np.argmin(area))
        if best is None or area[i] < best[0]:
            best = (area[i], xs[i], ys[i], off, g)
    if best is None:
        return None
    _, ox, oy, (bminx, bminy), b_geom = best
    ga = affinity.translate(a_geom, -aminx + ax0 * cell, -aminy + ay0 * cell)
    gb = affinity.translate(b_geom, -bminx + ox * cell, -bminy + oy * cell)
    x0 = min(ga.bounds[0], gb.bounds[0]); y0 = min(ga.bounds[1], gb.bounds[1])
    ga, gb = affinity.translate(ga, -x0, -y0), affinity.translate(gb, -x0, -y0)
    union = ga.union(gb)
    bx0, by0, bx1, by1 = union.bounds
    eff_pair = (ga.area + gb.area) / ((bx1 - bx0) * (by1 - by0))
    gx0, gy0, gx1, gy1 = part.geom.bounds
    eff_single = part.geom.area / ((gx1 - gx0) * (gy1 - gy0))
    cluster = PolyPart(part.name + " x2", union, 1, part.rotate, [(part.name, ga), (part.name, gb)])
    return cluster, eff_pair, eff_single


def _nest_once(jobs, sheet_w, sheet_h, margin, spacing, rot_step, cell, pad, progress):
    jobs = sorted(jobs, key=lambda p: -p.geom.area)
    total = sum(int(p.qty) * len(p.members or [1]) for p in jobs)
    grids: list[_Grid] = []
    done = 0
    for part in jobs:
        variants = _variants(part, rot_step, cell, pad)
        for _ in range(int(part.qty)):
            placed = None
            for g in grids:
                placed = g.best(variants)
                if placed:
                    grid = g
                    break
            if not placed:
                grid = _Grid(sheet_w, sheet_h, margin, spacing, cell)
                placed = grid.best(variants)
                if not placed:
                    raise ValueError(f"Part '{part.name}' does not fit on the sheet")
                grids.append(grid)
            k, ox, oy = placed
            ang, m, (minx, miny), g_rot = variants[k]
            grid.commit(m, ox, oy)
            c = part.geom.centroid
            members = part.members or [(part.name, part.geom)]
            for name, mg in members:
                if part.members:
                    mg = affinity.rotate(mg, ang, origin=(c.x, c.y)) if ang else mg
                else:
                    mg = g_rot
                tg = affinity.translate(mg, -minx + ox * cell, -miny + oy * cell)
                x0, y0, x1, y1 = tg.bounds
                grid.sheet.placements.append(Placement(
                    name, x0, y0, x1 - x0, y1 - y0, ang % 360 != 0, float(ang),
                    float(tg.area), geom_to_json(tg)))
                done += 1
                if progress:
                    progress(done, total)
    return [g.sheet for g in grids]


def nest_polygons(parts, sheet_w, sheet_h, margin=0.0, spacing=0.0, rot_step=90.0, cell=None, progress=None):
    """Nest PolyParts on identical sheets. Returns list[Sheet] (best of the plain and the paired strategy)."""
    if sheet_w - 2 * margin <= 0 or sheet_h - 2 * margin <= 0:
        raise ValueError("Margin is too large for the sheet")
    cell = cell or max(sheet_w, sheet_h) / 800.0
    pad = spacing / 2 + PAD_FACTOR * cell

    # strategy B: nest rigid pairs (only where a pair is clearly tighter than the single part)
    paired, any_pair = [], False
    for p in parts:
        n_pairs = int(p.qty) // 2
        found = None
        if n_pairs and p.members is None:
            try:
                found = _best_pair(p, rot_step, cell, pad)
            except Exception:  # noqa: BLE001  (pairing is an optimisation only)
                found = None
        if found and found[1] >= 1.15 * found[2]:
            cluster = found[0]
            cluster.qty = n_pairs
            paired.append(cluster)
            any_pair = True
            if int(p.qty) % 2:
                paired.append(PolyPart(p.name, p.geom, 1, p.rotate))
        else:
            paired.append(p)

    strategies = [parts] + ([paired] if any_pair else [])
    results, first_err = [], None
    for i, jobs in enumerate(strategies):
        def prog(d, t, i=i):
            if progress:
                progress(i * t + d, len(strategies) * t)
        try:
            results.append(_nest_once(jobs, sheet_w, sheet_h, margin, spacing, rot_step, cell, pad, prog))
        except ValueError as e:
            first_err = first_err or e
    if not results:
        raise first_err
    return min(results, key=lambda sh: (len(sh), sh[-1].used_area))
