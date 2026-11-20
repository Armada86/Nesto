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
* Regular scrap: after the sheet count, layouts are ranked by the quality of the usable offcuts:
  large, clean and SQUARE-ish rectangles (area x squareness) beat long thin strips. Small parts are therefore sent into gaps between
  parts - on any sheet - instead of cutting into the biggest free rectangle of a sheet.
* Multi-start: deterministic baselines first, then randomised restarts (part
  order, rule, noisy choice of rotation) until the time budget is spent. The best
  layout wins: fewest sheets, then the emptiest last sheet, then the shortest nest.
"""
from __future__ import annotations

import math
import random
import time
from dataclasses import dataclass, field, replace

import numpy as np
import scipy.fft
from matplotlib.path import Path
from scipy.ndimage import binary_dilation, find_objects, label
from scipy.signal import fftconvolve
from shapely import affinity

from nesting import Placement, Sheet
from shapes import geom_to_json

PAD_FACTOR = 0.75  # > sqrt(2)/2: any cell touched by the part has its centre inside the padded part
RULES = ("xend", "env", "contact", "yend")
ORDERS = ("area", "long", "perimeter", "width", "height")
SMALL_PART = 0.08  # a part smaller than this fraction of the usable sheet is "small": it hunts for gaps
REGULAR = 0.7      # a free region is "regular scrap" if its biggest rectangle fills >= 70 % of the region
MAX_SCRAP = 4      # regular scrap rectangles tracked per sheet
BIG = 1e9          # damage to the biggest free rectangle outranks the placement rule


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
    ii: np.ndarray | None = None


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


def _aligned_angles(geom, max_edges=4):
    """Rotations that lay the longest straight edges of the part flat (horizontal / vertical)."""
    hull = geom.convex_hull
    if hull.geom_type != "Polygon":
        return []
    pts = list(hull.exterior.coords)
    edges = []
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        length = math.hypot(x1 - x0, y1 - y0)
        if length > 0:
            edges.append((length, math.degrees(math.atan2(y1 - y0, x1 - x0))))
    edges.sort(reverse=True)
    long_edges = [e for e in edges if e[0] >= 0.3 * edges[0][0]]
    if len(long_edges) > max_edges:  # round / many-sided outline: no distinguished edge
        return []
    out = set()
    for _, theta in long_edges:
        for k in range(4):
            out.add(round((-theta + 90 * k) % 360, 3))
    return sorted(out)


def _min_width(geom):
    """Smallest width of the part over all directions (rotating calipers on the convex hull)."""
    hull = geom.convex_hull
    if hull.geom_type != "Polygon":
        return 0.0
    pts = np.asarray(hull.exterior.coords)[:-1]
    best = float("inf")
    for i in range(len(pts)):
        d = pts[(i + 1) % len(pts)] - pts[i]
        n = math.hypot(*d)
        if n == 0:
            continue
        dist = np.abs((pts - pts[i]) @ np.array([-d[1], d[0]]) / n)
        best = min(best, float(dist.max()))
    return best


def _variants(part: PolyPart, step: float, cell: float, pad: float):
    """Distinct rotations of a part (cached on the part).

    Fixed steps (multiples of `step`) plus edge-aligned angles, so that e.g. the hypotenuse
    of a triangle can lie exactly horizontal whatever the step is.
    """
    key = (step, cell, pad)
    if key in part._cache:
        return part._cache[key]
    angles = [0.0]
    if part.rotate and step and step < 360:
        angles = [a * step for a in range(int(round(360 / step)))]
        for a in _aligned_angles(part.geom):
            if all(min(abs(a - b), 360 - abs(a - b)) > 0.05 for b in angles):
                angles.append(a)
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


def scrap_thresholds(sheet_w, sheet_h):
    """(min side, min area) a free rectangle needs to count as usable scrap - relative to the sheet.
    Pieces thinner than 15 % of the sheet's narrow side are waste."""
    return 0.15 * min(sheet_w, sheet_h), 0.02 * sheet_w * sheet_h


def squareness(w, h):
    """1.0 for a square, towards 0 for a long thin strip."""
    return min(w, h) / max(w, h) if max(w, h) > 0 else 0.0


def scrap_score(w, h):
    """Value of one scrap piece: its area weighted by how square it is."""
    return w * h * squareness(w, h)


def _largest_rect(free):
    """Largest all-True axis-aligned rectangle of a boolean grid: (area, x0, y0, x1, y1), half-open."""
    ny, nx = free.shape
    heights = [0] * nx
    best = (0, 0, 0, 0, 0)
    for y in range(ny):
        row = free[y].tolist()
        heights = [h + 1 if f else 0 for h, f in zip(heights, row)]
        stack = []  # (start index, height)
        for x in range(nx + 1):
            h = heights[x] if x < nx else 0
            start = x
            while stack and stack[-1][1] >= h:
                st, hh = stack.pop()
                area = hh * (x - st)
                if area > best[0]:
                    best = (area, st, y - hh + 1, x, y + 1)
                start = st
            stack.append((start, h))
    return best


def _coarsen(free, f):
    """Free map at 1/f resolution: a coarse cell is free only if all its fine cells are free."""
    ny, nx = free.shape
    cy, cx = ny // f, nx // f
    return free[:cy * f, :cx * f].reshape(cy, f, cx, f).all(axis=(1, 3))


def _regular_rects(free, f, cell, sheet_w, sheet_h, bounds, max_n=MAX_SCRAP):
    """Regular scrap of a free map: for every connected free region that
      * touches the usable sheet edge (enclosed pockets and holes in parts are not offcuts),
      * is at least as big as the size thresholds, and
      * is rectangular (its biggest rectangle fills >= REGULAR of it),
    the biggest rectangle in it. Returns [(x0, y0, x1, y1)] in coarse cells, best score (area x squareness) first."""
    min_side, min_area = scrap_thresholds(sheet_w, sheet_h)
    cs = f * cell
    min_side_c, min_cells = math.ceil(min_side / cs), math.ceil(min_area / (cs * cs))
    coarse = _coarsen(free, f)
    lab, _ = label(coarse)
    bx0, by0, bx1, by1 = (math.ceil(bounds[0] / f), math.ceil(bounds[1] / f),
                          math.floor(bounds[2] / f), math.floor(bounds[3] / f))
    out = []
    for k, sl in enumerate(find_objects(lab), 1):
        sub = lab[sl] == k
        region = int(sub.sum())
        if region < min_cells:
            continue
        if not (sl[1].start <= bx0 + 1 or sl[1].stop >= bx1 - 1 or sl[0].start <= by0 + 1 or sl[0].stop >= by1 - 1):
            continue  # does not reach the sheet edge: a pocket, not an offcut
        area, x0, y0, x1, y1 = _largest_rect(sub)
        if area < min_cells or min(x1 - x0, y1 - y0) < min_side_c or area / region < REGULAR:
            continue
        score = scrap_score(x1 - x0, y1 - y0)
        out.append((score, sl[1].start + x0, sl[0].start + y0, sl[1].start + x1, sl[0].start + y1))
    out.sort(reverse=True)
    return [r[1:] for r in out[:max_n]]


def _integral(v: _Var):
    if v.ii is None:
        h, w = v.mask.shape
        ii = np.zeros((h + 1, w + 1), dtype=np.int32)
        ii[1:, 1:] = v.mask.cumsum(0).cumsum(1)
        v.ii = ii
    return v.ii


def _overlap_map(v: _Var, rect, ny, nx):
    """Cells of the mask that would fall inside `rect` (fine cells) for every position (ny x nx)."""
    rx0, ry0, rx1, ry1 = rect
    h, w = v.mask.shape
    ii = _integral(v)
    ox, oy = np.arange(nx), np.arange(ny)
    ax0, ax1 = np.clip(rx0 - ox, 0, w), np.clip(rx1 - ox, 0, w)
    ay0, ay1 = np.clip(ry0 - oy, 0, h), np.clip(ry1 - oy, 0, h)
    return (ii[np.ix_(ay1, ax1)] - ii[np.ix_(ay0, ax1)] - ii[np.ix_(ay1, ax0)] + ii[np.ix_(ay0, ax0)]).astype(np.float64)


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
        self.bounds = (x0, y0, x1, y1)  # usable area in fine cells
        self.occ_pad = None
        self.xc = self.yc = 0  # extent of the nest so far (cells)
        self.cell = cell
        self.f = max(1, round(max(self.nx, self.ny) / 160))  # coarse factor for free-space analysis
        self.free_cells = self.free0 = int((self.occ == 0).sum())
        self._rect = None
        self.sheet = Sheet(sheet_w, sheet_h)

    def _regular(self, free=None):
        free = (self.occ == 0) if free is None else free
        return _regular_rects(free, self.f, self.cell, self.sheet.width, self.sheet.height, self.bounds)

    def protected(self):
        """Regular scrap of this sheet as fine-cell rectangles (cached until the next commit)."""
        if self._rect is None:
            self._rect = [(a * self.f, b * self.f, c * self.f, d * self.f) for a, b, c, d in self._regular()]
        return self._rect

    def scrap_rects(self):
        """Regular scrap in sheet units: [(x, y, w, h)]."""
        cs = self.f * self.cell
        return [(a * cs, b * cs, (c - a) * cs, (d - b) * cs) for a, b, c, d in self._regular()]

    def free_edge(self):
        """Length of the boundary between free and occupied space (compact, connected free space = short)."""
        free = self.occ == 0
        solid = ~free
        near = binary_dilation(solid, structure=np.array([[0, 1, 0], [1, 1, 1], [0, 1, 0]], bool))
        return float((free & near).sum()) * self.cell

    def scrap_value(self, free=None):
        """Regular scrap of this sheet in cells, each piece weighted by its squareness."""
        return sum(scrap_score(c - a, d - b) for a, b, c, d in self._regular(free)) * self.f ** 2

    def loss_if_placed(self, mask, ox, oy):
        """Regular scrap (cells) this sheet would lose if the mask were placed at (ox, oy)."""
        free = self.occ == 0
        h, w = mask.shape
        free[oy:oy + h, ox:ox + w] &= ~mask
        return self.scrap_value() - self.scrap_value(free)

    def best(self, variants, rule="xend", rng=None, noise=0.0, protect=False):
        """Best feasible (variant_index, ox, oy, damage) on this sheet, or None.

        With protect=True, positions that eat into the sheet's regular scrap are penalised
        (damage = cells taken from it); damage outranks the placement rule.
        """
        if self.occ_pad is None:  # also needed to break ties by contact (see below)
            self.occ_pad = np.pad(self.occ, 1, constant_values=1.0)
        rects = self.protected() if protect else None
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
            score = np.asarray(score, dtype=np.float64)
            dmg = sum(_overlap_map(v, r, *feas.shape) for r in rects) if rects else None
            if dmg is not None:
                score = score + BIG * dmg
            score = np.where(feas, score, np.inf)
            idx = np.unravel_index(np.argmin(score), score.shape)
            oy, ox = int(idx[0]), int(idx[1])
            # equal scores (e.g. a triangle and its mirror image, same bounding box): the orientation that touches
            # its neighbours / the sheet edge along more of its outline wins, so flat sides sit flush
            touch = float((self.occ_pad[oy:oy + h + 2, ox:ox + w + 2] * _ring(v)).sum())
            cands.append((float(score[idx]), -touch, k, ox, oy, float(dmg[idx]) if dmg is not None else 0.0))
        if not cands:
            return None
        cands.sort()
        if rng is not None and noise and len(cands) > 1 and rng.random() < noise:
            return cands[1][2:]
        return cands[0][2:]

    def commit(self, m, ox, oy):
        h, w = m.shape
        self.occ[oy:oy + h, ox:ox + w][m] = 1.0
        self.occ_pad = None
        self._rect = None
        self.free_cells = int((self.occ == 0).sum())
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


def _cluster_options(parts, step, cell, spacing):
    """For each part with qty>=2: its tight pair / quad prototypes (or None when not clearly tighter)."""
    opts = []
    for p in parts:
        o = {"part": p, "pair": None, "quad": None}
        if int(p.qty) >= 2 and p.members is None:
            try:
                found = _best_pair(p, step, cell, spacing)
            except Exception:  # noqa: BLE001 - pairing is only an optimisation
                found = None
            if found and found[1] >= 1.15 * found[2]:
                o["pair"] = found[0]
                if int(p.qty) >= 4:  # pair of pairs
                    try:
                        q = _best_pair(PolyPart(found[0].name, found[0].geom, 2, found[0].rotate, found[0].members),
                                       step, cell, spacing)
                    except Exception:  # noqa: BLE001
                        q = None
                    if q and q[1] >= 1.05 * found[1]:
                        o["quad"] = q[0]
        opts.append(o)
    return opts


def _single(p, qty):
    return replace(p, qty=qty)  # shares the variant cache with p


def _compose(opts, mode):
    """Job list: 'plain' = all single parts, 'full' = as many pairs/quads as possible,
    list of k = k pairs per part (rest single)."""
    jobs = []
    for i, o in enumerate(opts):
        p, qty = o["part"], int(o["part"].qty)
        if mode == "plain" or o["pair"] is None:
            jobs.append(_single(p, qty))
            continue
        if mode == "full":
            n_pairs = qty // 2
            if o["quad"] is not None and n_pairs >= 2:
                jobs.append(replace(o["quad"], qty=n_pairs // 2))
                n_pairs %= 2
            if n_pairs:
                jobs.append(replace(o["pair"], qty=n_pairs))
            if qty % 2:
                jobs.append(_single(p, 1))
        else:
            k = max(0, min(int(mode[i]), qty // 2))
            if k:
                jobs.append(replace(o["pair"], qty=k))
            if qty - 2 * k:
                jobs.append(_single(p, qty - 2 * k))
    return jobs


# -------------------------------------------------------------------- decode
def _nest_once(jobs, sheet_w, sheet_h, margin, spacing, rot_step, cell, pad, rule, rng=None, noise=0.0,
               protect=False):
    grids: list[_Grid] = []
    for part in jobs:
        variants = _variants(part, rot_step, cell, pad)
        c = part.geom.centroid
        members = _flat_members(part)
        min_cells = min(int(v.mask.sum()) for v in variants)
        for _ in range(int(part.qty)):
            placed = None
            small = protect and grids and min_cells <= SMALL_PART * grids[0].free0
            if small:  # hunt for a gap on ANY sheet that does not cut into that sheet's regular scrap
                found, options = None, []
                for g in grids:
                    if g.free_cells < min_cells:
                        continue
                    cand = g.best(variants, rule, rng, noise, protect=True)
                    if cand is None:
                        continue
                    if cand[3] == 0:        # a gap that costs no regular scrap at all
                        found = (g, cand)
                        break
                    options.append((g, cand))
                if not found and options:   # no free gap anywhere: take the spot that costs the least scrap
                    found = min(options, key=lambda gc: gc[0].loss_if_placed(variants[gc[1][0]].mask, gc[1][1], gc[1][2]))
                if found:
                    grid, placed = found
            else:
                for g in grids:
                    placed = g.best(variants, rule, rng, noise, protect=protect)
                    if placed:
                        grid = g
                        break
            if not placed:
                grid = _Grid(sheet_w, sheet_h, margin, spacing, cell)
                placed = grid.best(variants, rule, rng, noise, protect=protect)
                if not placed:
                    raise ValueError(f"Part '{part.name}' does not fit on the sheet")
                grids.append(grid)
            k, ox, oy = placed[:3]
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
    for g in grids:
        g.sheet.scrap = g.scrap_rects()
        g.sheet.free_edge = g.free_edge()
    return [g.sheet for g in grids]


def free_rectangles(sheet, margin=0.0, k=MAX_SCRAP):
    """Regular scrap of a finished sheet (see _regular_rects): [(x, y, w, h)], biggest first."""
    cell = max(sheet.width, sheet.height) / 700.0
    nx, ny = int(math.ceil(sheet.width / cell)), int(math.ceil(sheet.height / cell))
    occ = np.ones((ny, nx), dtype=bool)
    x0, x1 = max(0, math.ceil(margin / cell)), min(nx, math.floor((sheet.width - margin) / cell))
    y0, y1 = max(0, math.ceil(margin / cell)), min(ny, math.floor((sheet.height - margin) / cell))
    if x1 <= x0 or y1 <= y0:
        return []
    occ[y0:y1, x0:x1] = False
    from shapes import geom_from_json
    from shapely.geometry import box
    for p in sheet.placements:
        g = geom_from_json(p.polys) if p.polys else box(p.x, p.y, p.x + p.w, p.y + p.h)
        m, (minx, miny) = _mask(g, cell, 1.25 * cell)  # conservative: every touched cell is blocked
        ox, oy = int(math.floor(minx / cell)), int(math.floor(miny / cell))
        h, w = m.shape
        sx0, sy0 = max(0, ox), max(0, oy)
        sx1, sy1 = min(nx, ox + w), min(ny, oy + h)
        if sx1 > sx0 and sy1 > sy0:
            occ[sy0:sy1, sx0:sx1] |= m[sy0 - oy:sy1 - oy, sx0 - ox:sx1 - ox]
    f = max(1, round(max(nx, ny) / 160))
    cs = f * cell
    rects = _regular_rects(~occ, f, cell, sheet.width, sheet.height, (x0, y0, x1, y1), k)
    return [(a * cs, b * cs, (c - a) * cs, (d - b) * cs) for a, b, c, d in rects]


def _fitness(sheets):
    """Smaller is better: fewest sheets; then the best scrap pieces (area x squareness, compared best piece first,
    then the next ...: one big square beats a long thin strip); then the biggest pieces by plain area, again biggest
    first (keeps an unavoidable strip whole); then the most compact free space (shortest free/occupied boundary:
    flat sides flush against each other, scrap in one connected piece); then the emptiest last sheet; then the
    shortest nest."""
    last = sheets[-1]
    sheet_area = last.width * last.height
    rects = [r for s in sheets for r in s.scrap]
    pieces = sorted((-round(scrap_score(r[2], r[3]) / sheet_area, 2) for r in rects))[:8]
    areas = sorted((-round(r[2] * r[3] / sheet_area, 2) for r in rects))[:8]
    extent = max((p.x + p.w) for p in last.placements)
    edge = sum(s.free_edge for s in sheets)
    return (len(sheets), tuple(pieces), tuple(areas), round(edge / 500.0), round(last.used_area / sheet_area, 3), extent)


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
    for p in parts:  # fail fast on parts that cannot fit at any rotation
        if _min_width(p.geom) > min(sheet_w, sheet_h) - 2 * margin + 1e-6 or p.geom.area > (sheet_w - 2 * margin) * (sheet_h - 2 * margin):
            raise ValueError(f"Part '{p.name}' does not fit on the sheet")
    cell = cell or max(sheet_w, sheet_h) / 800.0
    pad = spacing / 2 + PAD_FACTOR * cell
    rng = random.Random(seed)
    t0 = time.monotonic()

    opts = _cluster_options(parts, rot_step, cell, spacing)
    paired_idx = [i for i, o in enumerate(opts) if o["pair"] is not None]
    any_cluster = bool(paired_idx)
    sets = {"plain": _compose(opts, "plain")}
    if any_cluster:
        sets["paired"] = _compose(opts, "full")

    plan = []  # deterministic baselines, most promising first: (mode, rule, order, noise, protect)
    for protect in (False, True):
        for name in (["paired"] if any_cluster else []) + ["plain"]:
            plan.append((name, "xend", "area", 0.0, protect))
    n_base_min = len(plan)  # always run at least these
    if any_cluster:  # mix of pairs and singles: k pairs for the biggest paired part, others full
        big = max(paired_idx, key=lambda i: opts[i]["part"].geom.area * int(opts[i]["part"].qty))
        maxk = int(opts[big]["part"].qty) // 2
        ks = sorted({max(1, round(maxk * f)) for f in (0.8, 0.6, 0.4, 0.2)} | set(range(max(1, maxk - 3), maxk)), reverse=True)
        for k in ks:
            if 1 <= k < maxk:
                mode = [int(o["part"].qty) // 2 if o["pair"] is not None else 0 for o in opts]
                mode[big] = k
                plan.append((tuple(mode), "xend", "area", 0.0, True))
    for name in (["paired"] if any_cluster else []) + ["plain"]:
        for rule in ("env", "contact", "yend"):
            plan.append((name, rule, "area", 0.0, True))

    best, best_fit, errors, runs = None, None, [], 0
    i = 0
    while True:
        elapsed = time.monotonic() - t0
        if i < len(plan):
            if i >= n_base_min and elapsed >= time_limit:
                break
            setname, rule, order, noise, protect = plan[i]
        else:
            if elapsed >= time_limit:
                break
            kind = rng.choice(["plain", "paired", "mixed", "mixed"] if any_cluster else ["plain"])
            if kind == "mixed":
                setname = tuple(rng.randint(0, int(o["part"].qty) // 2) if o["pair"] is not None else 0 for o in opts)
            else:
                setname = kind
            rule = rng.choice(RULES)
            order = rng.choice(ORDERS + ("shuffle",))
            noise = rng.choice((0.0, 0.1, 0.25))
            protect = rng.random() < 0.65
        i += 1
        base_jobs = sets[setname] if isinstance(setname, str) else _compose(opts, list(setname))
        jobs = _order(base_jobs, order, rng)
        try:
            res = _nest_once(jobs, sheet_w, sheet_h, margin, spacing, rot_step, cell, pad, rule,
                             rng if noise else None, noise, protect)
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
                info.update(set=setname if isinstance(setname, str) else "mixed", rule=rule, order=order, noise=noise,
                            protect=protect)
        if progress:
            frac = min(1.0, (time.monotonic() - t0) / max(time_limit, 1e-9)) if time_limit else min(1.0, i / n_base_min)
            big = f" · best scrap piece ≈ {-best_fit[1][0]:.0%} of a sheet" if best_fit[1] else ""
            progress(frac, f"layouts tried: {runs} · best: {best_fit[0]} sheet(s){big}")
    if best is None:
        raise errors[0]
    if info is not None:
        info.update(runs=runs, seconds=time.monotonic() - t0, fitness=best_fit)
    return best
