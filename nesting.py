"""2D rectangular nesting engine (MaxRects, multi-sheet, optional rotation).

Fast exact engine for rectangular parts. Irregular shapes use nesting_poly.py.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from itertools import product

EPS = 1e-9


@dataclass
class Part:
    name: str
    width: float
    height: float
    qty: int = 1
    rotate: bool = True


@dataclass
class Placement:
    part: str
    x: float
    y: float
    w: float  # placed width (after rotation)
    h: float
    rotated: bool
    angle: float = 0.0
    area: float | None = None  # real area for non-rectangular parts
    polys: list | None = None  # absolute outline on the sheet (see shapes.geom_to_json)


@dataclass
class Sheet:
    width: float
    height: float
    placements: list[Placement] = field(default_factory=list)
    scrap: list = field(default_factory=list)  # largest clean free rectangles [(x, y, w, h)]

    @property
    def used_area(self) -> float:
        return sum(p.area if p.area is not None else p.w * p.h for p in self.placements)

    @property
    def utilization(self) -> float:
        return self.used_area / (self.width * self.height)


class _MaxRectsBin:
    def __init__(self, w: float, h: float, heuristic: str):
        self.free = [(0.0, 0.0, w, h)]
        self.heuristic = heuristic

    def _score(self, fr, w, h):
        fx, fy, fw, fh = fr
        lw, lh = fw - w, fh - h
        if self.heuristic == "baf":  # best area fit
            return (fw * fh - w * h, min(lw, lh))
        if self.heuristic == "blsf":  # best long side fit
            return (max(lw, lh), min(lw, lh))
        if self.heuristic == "bl":  # bottom-left
            return (fy + h, fx)
        return (min(lw, lh), max(lw, lh))  # bssf

    def insert(self, w, h, can_rotate):
        best = None
        for fr in self.free:
            for rot, (pw, ph) in ((False, (w, h)), (True, (h, w))):
                if rot and not can_rotate:
                    continue
                if pw <= fr[2] + EPS and ph <= fr[3] + EPS:
                    s = self._score(fr, pw, ph)
                    if best is None or s < best[0]:
                        best = (s, fr[0], fr[1], pw, ph, rot)
        if best is None:
            return None
        _, x, y, pw, ph, rot = best
        self._split(x, y, pw, ph)
        return x, y, pw, ph, rot

    def _split(self, x, y, w, h):
        new = []
        for fx, fy, fw, fh in self.free:
            if x >= fx + fw - EPS or x + w <= fx + EPS or y >= fy + fh - EPS or y + h <= fy + EPS:
                new.append((fx, fy, fw, fh))
                continue
            if x > fx + EPS:
                new.append((fx, fy, x - fx, fh))
            if x + w < fx + fw - EPS:
                new.append((x + w, fy, fx + fw - x - w, fh))
            if y > fy + EPS:
                new.append((fx, fy, fw, y - fy))
            if y + h < fy + fh - EPS:
                new.append((fx, y + h, fw, fy + fh - y - h))
        # drop free rects contained in others
        keep = []
        for i, a in enumerate(new):
            contained = False
            for j, b in enumerate(new):
                if i != j and a[0] >= b[0] - EPS and a[1] >= b[1] - EPS \
                        and a[0] + a[2] <= b[0] + b[2] + EPS and a[1] + a[3] <= b[1] + b[3] + EPS \
                        and (a != b or j < i):
                    contained = True
                    break
            if not contained:
                keep.append(a)
        self.free = keep


def _pack(items, sheet_w, sheet_h, heuristic, spacing):
    """items: list of (part, w, h). Returns list[Sheet] or raises if a part never fits."""
    sheets: list[tuple[Sheet, _MaxRectsBin]] = []
    for part, w, h in items:
        # inflate by spacing; sheet is inflated too so the outer edge needs no gap
        iw, ih = w + spacing, h + spacing
        placed = False
        for sheet, binr in sheets:
            r = binr.insert(iw, ih, part.rotate)
            if r:
                _add(sheet, part, r, w, h, spacing)
                placed = True
                break
        if not placed:
            sheet = Sheet(sheet_w, sheet_h)
            binr = _MaxRectsBin(sheet_w + spacing, sheet_h + spacing, heuristic)
            r = binr.insert(iw, ih, part.rotate)
            if not r:
                raise ValueError(f"Part '{part.name}' ({w} x {h}) does not fit on the sheet")
            _add(sheet, part, r, w, h, spacing)
            sheets.append((sheet, binr))
    return [s for s, _ in sheets]


def _add(sheet, part, r, w, h, spacing):
    x, y, pw, ph, rot = r
    sheet.placements.append(Placement(part.name, x, y, pw - spacing, ph - spacing, rot, 90.0 if rot else 0.0))


def nest(parts, sheet_w, sheet_h, margin=0.0, spacing=0.0):
    """Nest parts on identical sheets. Tries several heuristics/orderings, returns best.

    Best = fewest sheets, then least area on the last sheet (leaves a reusable offcut).
    """
    usable_w, usable_h = sheet_w - 2 * margin, sheet_h - 2 * margin
    if usable_w <= 0 or usable_h <= 0:
        raise ValueError("Margin is too large for the sheet")
    expanded = [(p, p.width, p.height) for p in parts for _ in range(int(p.qty))]
    if not expanded:
        return []
    orders = {
        "area": lambda t: -(t[1] * t[2]),
        "long_side": lambda t: -max(t[1], t[2]),
        "width": lambda t: -t[1],
        "height": lambda t: -t[2],
        "perimeter": lambda t: -(t[1] + t[2]),
    }
    best = None
    for (oname, key), heur in product(orders.items(), ("bssf", "baf", "blsf", "bl")):
        sheets = _pack(sorted(expanded, key=key), usable_w, usable_h, heur, spacing)
        # fewer sheets first, then the emptier the last sheet the better (reusable offcut)
        score = (len(sheets), sheets[-1].used_area)
        if best is None or score < best[0]:
            best = (score, sheets)
    sheets = best[1]
    # shift placements by margin and restore full sheet size
    for s in sheets:
        s.width, s.height = sheet_w, sheet_h
        for p in s.placements:
            p.x += margin
            p.y += margin
    return sheets


def summary(sheets):
    if not sheets:
        return {"sheets": 0, "parts": 0, "utilization": 0.0}
    total = sum(s.width * s.height for s in sheets)
    used = sum(s.used_area for s in sheets)
    return {
        "sheets": len(sheets),
        "parts": sum(len(s.placements) for s in sheets),
        "utilization": used / total,
    }
