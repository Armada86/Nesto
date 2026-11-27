"""Basic 2.5D CAM for a nested sheet: contour toolpaths for a router.

For every part on the sheet the outline is cut on the WASTE side (the tool centre runs on the outline offset OUTWARDS by
the tool radius) and every hole of a part is cut from inside the hole (offset INWARDS by the tool radius). The
toolpaths are offset in software, so the G-code has no cutter compensation (no G41 / G42).

Order: all inner contours of all parts first (nearest neighbour), then all outlines, so a part is still held by its
sheet while its holes are cut. Depth: equal passes of at most the tool's step-down, from the material top (Z0) down to
the thickness plus a small overcut; either a straight plunge at the start of each contour or a ramp along the contour.
Optional tabs on the last pass of the outlines hold the parts down.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from shapely.geometry import LineString, Polygon

from layout_edit import is_held, placement_geom
import machine_profile as machine

EPS = 1e-9
ARC_COUNT_SLACK = 0.01     # an arc of 90.03 degrees still gets the 14 pieces of a quarter circle (what SolidCAM does)


@dataclass
class Tool:
    number: int                       # T number = tool station (1 .. 26); the length offset H is the same number
    name: str = ""
    diameter: float = 6.0
    flutes: int = 2
    rpm: float = 12000.0
    feed: float = 4000.0              # mm/min, cutting
    plunge: float = 1500.0            # mm/min, plunging / ramping
    step_down: float = 6.0            # mm per pass
    coolant: str = "none"             # none | flood | air
    length: float = 0.0               # the length of the tool out of its holder (the header of the program)
    holder: str = ""                  # the holder (the header of the program)

    def problems(self) -> list[str]:
        out = []
        if not (1 <= int(self.number) <= machine.MAX_TOOL_NUMBER):
            out.append(f"T{self.number}: the tool number must be 1 to {machine.MAX_TOOL_NUMBER} (tool positions of the machine)")
        if self.diameter <= 0:
            out.append(f"T{self.number}: the diameter must be more than 0")
        if not (0 < self.rpm <= machine.MAX_RPM):
            out.append(f"T{self.number}: the spindle speed must be 1 to {machine.MAX_RPM:g} rpm")
        if not (0 < self.feed <= machine.MAX_FEED) or self.plunge <= 0:
            out.append(f"T{self.number}: the feed rates must be more than 0 (and at most {machine.MAX_FEED:g} mm/min)")
        if self.step_down <= 0:
            out.append(f"T{self.number}: the step-down must be more than 0")
        if self.coolant not in ("none", "flood", "air"):
            out.append(f"T{self.number}: coolant must be none, flood or air")
        return out


@dataclass
class CamJob:
    thickness: float = 18.0           # material thickness (Z0 is the top of the material, the table is at -thickness)
    overcut: float = 0.3              # how far the tool goes below the material, so the parts are cut through
    clearance: float = 5.0            # rapid height above the material top
    approach: float = 1.0             # the tool goes down at rapid speed to this height, then feeds
    climb: bool = True                # climb milling (outlines clockwise, holes counter-clockwise); False = conventional
    ramp: bool = True                 # go down along the contour (spiral) instead of a straight plunge
    tabs: int = 0                     # tabs per outline (0 = none)
    tab_width: float = 10.0
    tab_height: float = 2.0           # material left under a tab
    inner_tool: int = 1
    outer_tool: int = 1
    smoothing: float = machine.DEFAULT_SMOOTHING   # G05.1 Q1 R..  (0 = off)
    extension: str = machine.FILE_EXTENSION
    programmer: str = ""

    def depth(self) -> float:
        return self.thickness + self.overcut


@dataclass
class Contour:
    kind: str                          # "inner" (a hole) | "outer" (the outline of a part)
    part: str
    pts: list                          # closed ring of the TOOL CENTRE path [(x, y), ...], first == last
    tool: int
    src: list | None = None            # the closed ring of the PART (outline or hole) this path was made from
    radius: float = 0.0                # the tool radius
    key: tuple = (0.0, 0.0)            # lower left of the part (or hole): the left to right order
    start: tuple | None = None         # where the path must start (the top of a circle); None = the usual start point


@dataclass
class Plan:
    contours: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    skipped: int = 0


def _ring_length(pts) -> float:
    return sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(pts, pts[1:]))


def _signed_area(pts) -> float:
    return 0.5 * sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(pts, pts[1:]))


def _orient(pts, ccw: bool):
    return pts if (_signed_area(pts) > 0) == ccw else pts[::-1]


def _start_near(pts, near):
    """The ring (closed) re-started in the middle of one of its three longest edges, the one nearest to `near`: the
    tool enters and leaves in a straight stretch, not in a corner."""
    edges = sorted(range(len(pts) - 1), key=lambda i: -math.hypot(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1]))[:3]
    best = None
    for i in edges:
        m = ((pts[i][0] + pts[i + 1][0]) / 2, (pts[i][1] + pts[i + 1][1]) / 2)
        d = math.hypot(m[0] - near[0], m[1] - near[1])
        if best is None or d < best[0]:
            best = (d, i, m)
    _, i, m = best
    body = pts[:-1]
    return [m] + body[i + 1:] + body[:i + 1] + [m]


def contour_id(kind: str, key: tuple) -> str:
    """A name for a contour that stays the same while the layout does not change: its kind and the lower left corner of its part
    (H = hole, O = outline)."""
    return f"{'H' if kind == 'inner' else 'O'}@{key[0]:.2f},{key[1]:.2f}"


def _restart_at(pts, s):
    """The closed ring re-started at the point of the ring nearest to s (a vertex is reused, otherwise the point is inserted)."""
    best = None
    for i, (a, b) in enumerate(zip(pts, pts[1:])):
        dx, dy = b[0] - a[0], b[1] - a[1]
        L2 = dx * dx + dy * dy
        t = 0.0 if L2 < EPS else max(0.0, min(1.0, ((s[0] - a[0]) * dx + (s[1] - a[1]) * dy) / L2))
        q = (a[0] + t * dx, a[1] + t * dy)
        d = math.hypot(q[0] - s[0], q[1] - s[1])
        if best is None or d < best[0]:
            best = (d, i, t, q)
    _, i, t, q = best
    body = pts[:-1]
    n = len(body)
    if t < 1e-6:
        k = i
        return body[k:] + body[:k] + [body[k]]
    if t > 1 - 1e-6:
        k = (i + 1) % n
        return body[k:] + body[:k] + [body[k]]
    return [q] + body[i + 1:] + body[:i + 1] + [q]


def _lowleft_start(src, ccw: bool, r: float, toward_left: bool):
    """The start of a contour: the lowest left vertex of the part ring (lowest, the leftmost of those), then the point of the tool
    path that belongs to the beginning of the edge that leaves this vertex in the cutting direction (a radius to the side of the
    edge: to its left when toward_left, else to its right)."""
    ring = _orient(list(src), ccw)[:-1]
    n = len(ring)
    k = min(range(n), key=lambda i: (round(ring[i][1], 6), round(ring[i][0], 6)))
    v = ring[k]
    for j in range(1, n):
        w = ring[(k + j) % n]
        d = math.hypot(w[0] - v[0], w[1] - v[1])
        if d > 1e-9:
            ex, ey = (w[0] - v[0]) / d, (w[1] - v[1]) / d
            nx, ny = (-ey, ex) if toward_left else (ey, -ex)
            return (v[0] + r * nx, v[1] + r * ny)
    return v


def _circle_of(ring):
    """(cx, cy, R) when the closed ring is a circle cut into many equal straight pieces (all its vertices on one circle, evenly
    spaced), else None. A circular hole is stored like this in a layout; the true circle is what the tool should follow."""
    pts = [tuple(p) for p in ring]
    if len(pts) > 1 and pts[0] == pts[-1]:
        pts = pts[:-1]
    n = len(pts)
    if n < 12:
        return None
    cx, cy = sum(p[0] for p in pts) / n, sum(p[1] for p in pts) / n
    rs = [math.hypot(p[0] - cx, p[1] - cy) for p in pts]
    R = sum(rs) / n
    if R < 1e-6 or max(abs(r - R) for r in rs) > 0.002 + 1e-4 * R:
        return None
    ang = sorted(math.atan2(p[1] - cy, p[0] - cx) for p in pts)
    gaps = [b - a for a, b in zip(ang, ang[1:])] + [ang[0] + 2 * math.pi - ang[-1]]
    if max(gaps) > 1.5 * (2 * math.pi / n):
        return None
    return cx, cy, R


def _circle_ring(cx: float, cy: float, r: float, tol: float):
    """A counter-clockwise ring of equal straight pieces around the circle, starting at its top, at most `tol` from the circle."""
    k = max(4, math.ceil(2 * math.pi / _max_arc_step(r, tol) - ARC_COUNT_SLACK))
    pts = [(cx + r * math.cos(math.pi / 2 + 2 * math.pi * j / k), cy + r * math.sin(math.pi / 2 + 2 * math.pi * j / k)) for j in range(k)]
    pts[0] = (cx, cy + r)
    return pts + [pts[0]]


def _max_arc_step(r: float, tol: float) -> float:
    """The largest angle (rad) of one straight piece of an arc of radius r that stays within `tol` of the arc."""
    return 2 * math.acos(max(-1.0, 1 - tol / r)) if r > tol else math.pi / 2


def offset_ring(pts, r: float, outside: bool, tol: float, step: float | None = None):
    """The tool-centre path around a polygon ring made by hand: every straight edge is moved a radius to its side, a corner where
    the moved edges overlap is cut at their intersection, and a corner where they do not meet is rounded by an arc whose straight
    pieces are all the same length (as few as stay within `tol` of the true arc, the end points on the arc). pts: closed ring.
    outside: the path runs outside the polygon (an outline) or inside it (a hole). Returns a closed ring [(x, y), ...]."""
    step = step or _max_arc_step(r, tol)
    ring = [tuple(p) for p in pts]
    if ring[0] == ring[-1]:
        ring = ring[:-1]
    if _signed_area(ring + [ring[0]]) < 0:
        ring.reverse()                                        # counter-clockwise: the inside is on the left
    cl = []                                                   # without repeated points and straight-through points
    for i, p in enumerate(ring):
        a, b = ring[i - 1], ring[(i + 1) % len(ring)]
        if math.hypot(p[0] - a[0], p[1] - a[1]) < 1e-9:
            continue
        cross = (p[0] - a[0]) * (b[1] - p[1]) - (p[1] - a[1]) * (b[0] - p[0])
        dot = (p[0] - a[0]) * (b[0] - p[0]) + (p[1] - a[1]) * (b[1] - p[1])
        if abs(cross) < 1e-9 and dot > 0:
            continue
        cl.append(p)
    n = len(cl)

    def unit(a, b):
        d = math.hypot(b[0] - a[0], b[1] - a[1])
        return ((b[0] - a[0]) / d, (b[1] - a[1]) / d)

    out = []
    for i in range(n):
        p = cl[i]
        d1, d2 = unit(cl[i - 1], p), unit(p, cl[(i + 1) % n])
        side = (lambda d: (d[1], -d[0])) if outside else (lambda d: (-d[1], d[0]))      # the normal on the side of the path
        n1, n2 = side(d1), side(d2)
        cross = d1[0] * d2[1] - d1[1] * d2[0]
        dot = d1[0] * d2[0] + d1[1] * d2[1]
        if (cross > 0) == outside:                            # the moved edges do not meet: an arc from n1 to n2
            phi = math.atan2(cross, dot)
            if abs(phi) <= 0.5 * step:                        # a tiny turn (a curve cut into many short edges): one point is enough
                m = math.hypot(n1[0] + n2[0], n1[1] + n2[1]) or 1.0
                out.append((p[0] + r * (n1[0] + n2[0]) / m, p[1] + r * (n1[1] + n2[1]) / m))
                continue
            k = max(1, math.ceil(abs(phi) / step - ARC_COUNT_SLACK))
            for j in range(k + 1):
                c, s_ = math.cos(phi * j / k), math.sin(phi * j / k)
                out.append((p[0] + r * (n1[0] * c - n1[1] * s_), p[1] + r * (n1[0] * s_ + n1[1] * c)))
        else:                                                 # they overlap: the corner of the moved edges
            f = r / (1 + dot)
            out.append((p[0] + f * (n1[0] + n2[0]), p[1] + f * (n1[1] + n2[1])))
    out.append(out[0])
    return out


def _quad_segs(r: float, tol: float | None, default: int = 10) -> int:
    """Segments per quarter circle so that the chord is at most `tol` away from the arc (default: a fixed number)."""
    if not tol or r <= tol:
        return default
    return max(4, math.ceil((math.pi / 4) / math.acos(1 - tol / r)))


def _tool_path(coords, r: float, outside: bool, tol: float | None, ref):
    """The tool-centre path around a part ring. With a tolerance: made by hand (offset_ring) when that agrees with the shapely
    offset `ref` (a Polygon) and does not cross itself; otherwise the shapely offset itself."""
    fallback = [tuple(c) for c in ref.simplify(0.005).exterior.coords]
    if not tol:
        return fallback
    try:
        pts = offset_ring(coords, r, outside, tol)
        ring = LineString(pts)
        if ring.is_simple or (pts[0] == pts[-1] and LineString(pts[:-1] + [pts[0]]).is_ring):
            if LineString(pts).hausdorff_distance(ref.exterior) <= max(0.03, 3 * tol) and abs(Polygon(pts).area - ref.area) <= 0.01 * ref.area + 1.0:
                return pts
    except (ValueError, ZeroDivisionError):
        pass
    return fallback


def _tool_for(kind: str, job: CamJob, tools: dict):
    return tools.get(job.inner_tool if kind == "inner" else job.outer_tool)


def plan_sheet(sheet: dict, job: CamJob, tools: dict, margin: float = 0.0, spacing: float = 0.0, start: str = "auto",
               order: str = "nearest", arc_tol: float | None = None, custom_order: list | None = None) -> Plan:
    """The contours of a sheet (a stored layout dict) in cutting order. `tools`: {number: Tool}.
    start: "auto" = in the middle of one of the longest edges, near the previous contour; "lowleft" = at the lowest left vertex of
    the part. order: "nearest" = nearest neighbour; "left_right" = by the lower left corner of the part, from left to right.
    arc_tol: the largest distance of a chord from the arc (mm); None = 10 segments per quarter circle.
    custom_order: contour ids (contour_id) in the order they are cut; it replaces `order`; contours that are not in the list are
    cut after them (holes first, then outlines, from left to right)."""
    plan = Plan()
    inner_tool, outer_tool = tools.get(job.inner_tool), tools.get(job.outer_tool)
    for what, t, n in (("inner contours (holes)", inner_tool, job.inner_tool), ("outlines", outer_tool, job.outer_tool)):
        if t is None:
            plan.warnings.append(f"The tool for the {what} (T{n}) is not defined in the tool library.")
    if inner_tool is None or outer_tool is None:
        return plan
    parts = [(p["part"], placement_geom(p)) for p in sheet["placements"] if not is_held(p)]
    geoms = [g for _, g in parts]
    inner, outer = [], []
    too_close, too_small, gouge_names = 0, [], []
    for idx, (name, g) in enumerate(parts):
        polys = [g] if g.geom_type == "Polygon" else [x for x in g.geoms if x.geom_type == "Polygon"]
        others = [geoms[k] for k in range(len(geoms)) if k != idx]
        for poly in polys:
            r = outer_tool.diameter / 2
            ref = Polygon(poly.exterior).buffer(r, quad_segs=_quad_segs(r, arc_tol))
            pts = _tool_path(list(poly.exterior.coords), r, True, arc_tol, ref)
            # the tool must not touch the neighbouring parts: they have to be at least a tool diameter apart
            if others:
                line = LineString(pts)
                if min(line.distance(o) for o in others) < r - 0.02:
                    too_close += 1
                    gouge_names.append(name)
            bx = poly.bounds
            outer.append(Contour("outer", name, pts, job.outer_tool, list(poly.exterior.coords), r, (round(bx[0], 3), round(bx[1], 3))))
            for hole in poly.interiors:
                ri = inner_tool.diameter / 2
                shrunk = Polygon(hole).buffer(-ri, quad_segs=_quad_segs(ri, arc_tol))
                if shrunk.is_empty:
                    too_small.append(name)
                    plan.skipped += 1
                    continue
                circle = _circle_of(hole.coords) if arc_tol else None
                if circle and circle[2] - ri > 1e-6:           # a round hole: the true circle of the tool centre, not the 32-gon
                    hb = Polygon(hole).bounds
                    path = _circle_ring(circle[0], circle[1], circle[2] - ri, arc_tol)
                    inner.append(Contour("inner", name, path, job.inner_tool, list(hole.coords), ri,
                                         (round(hb[0], 3), round(hb[1], 3)), path[0]))
                    continue
                for sp in ([shrunk] if shrunk.geom_type == "Polygon" else list(shrunk.geoms)):
                    hb = Polygon(hole).bounds
                    hpts = (_tool_path(list(hole.coords), ri, False, arc_tol, sp) if shrunk.geom_type == "Polygon"
                            else [tuple(c) for c in sp.simplify(0.005).exterior.coords])
                    inner.append(Contour("inner", name, hpts, job.inner_tool,
                                         list(hole.coords), ri, (round(hb[0], 3), round(hb[1], 3))))
    if too_close:
        names = ", ".join(sorted(set(gouge_names))[:6])
        plan.warnings.append(f"{too_close} outline(s) have a neighbouring part closer than the tool diameter "
                             f"({outer_tool.diameter:g} mm; the nest used a part spacing of {spacing:g} mm): the tool would cut into "
                             f"the neighbour ({names}). Nest again with a part spacing of at least {outer_tool.diameter:g} mm.")
    if too_small:
        plan.warnings.append(f"{len(too_small)} hole(s) are smaller than the tool ({inner_tool.diameter:g} mm) and are not cut: "
                             f"{', '.join(sorted(set(too_small))[:6])}.")
    if margin and outer_tool.diameter / 2 > margin + 1e-9:
        plan.warnings.append(f"The edge margin ({margin:g} mm) is smaller than the tool radius ({outer_tool.diameter / 2:g} mm): "
                             "the tool leaves the sheet at its edge.")
    if job.thickness <= 0:
        plan.warnings.append("Enter the material thickness.")
    # cutting order: the inner contours of all parts first, then the outlines (or the order that was asked for)
    pos = (0.0, 0.0)
    ordered = []

    def finish(best, ccw_for_climb, pos):
        ccw = ccw_for_climb if job.climb else not ccw_for_climb
        ring = _orient(best.pts, ccw)
        if start == "lowleft" and best.start:
            ring = _restart_at(ring, best.start)               # a circle starts at its top
        elif start == "lowleft" and best.src:
            # outline: the tool runs outside the part, hole: inside the hole; the side is to the left of the travel direction
            # for a clockwise outline / counter-clockwise hole
            toward_left = (best.kind == "outer") != ccw
            ring = _restart_at(ring, _lowleft_start(best.src, ccw, best.radius, toward_left))
        else:
            ring = _start_near(ring, pos)
        return Contour(best.kind, best.part, ring, best.tool, best.src, best.radius, best.key, best.start)

    if custom_order:
        rank = {cid: i for i, cid in enumerate(custom_order)}
        default = sorted(inner, key=lambda c: c.key) + sorted(outer, key=lambda c: c.key)
        todo = sorted(default, key=lambda c: rank.get(contour_id(c.kind, c.key), len(rank) + default.index(c)))
        for c in todo:
            done = finish(c, c.kind == "inner", pos)
            ordered.append(done)
            pos = done.pts[0]
    else:
        for group, ccw_for_climb in ((inner, True), (outer, False)):
            todo = sorted(group, key=lambda c: c.key) if order == "left_right" else list(group)
            while todo:
                if order == "left_right":
                    best = todo.pop(0)
                else:
                    best = min(todo, key=lambda c: min(math.hypot(x - pos[0], y - pos[1]) for x, y in c.pts))
                    todo.remove(best)
                done = finish(best, ccw_for_climb, pos)
                ordered.append(done)
                pos = done.pts[0]
    plan.contours = ordered
    return plan


# ------------------------------------------------------------------------------------------------ moves
# A move is (code, x, y, z, feed): code "G0" (rapid) or "G1" (feed); feed None for rapids.
def _levels(job: CamJob, tool: Tool) -> list[float]:
    depth = job.depth()
    n = max(1, math.ceil(depth / tool.step_down - 1e-9))
    return [-depth * k / n for k in range(1, n + 1)]


def _with_tabs(ring, z_cut, z_tab, n, width, plunge):
    """Moves around a ring at depth z_cut with n tabs (the tool lifts to z_tab for `width` mm)."""
    pts = ring
    total = _ring_length(pts)
    spans = []
    for k in range(n):
        c = (k + 0.5) * total / n
        spans.append((max(0.0, c - width / 2), min(total, c + width / 2)))
    cum = [0.0]
    for a, b in zip(pts, pts[1:]):
        cum.append(cum[-1] + math.hypot(b[0] - a[0], b[1] - a[1]))
    cuts = sorted({0.0, total, *[s for sp in spans for s in sp]})
    def at(s):                                    # point at arc length s
        for i in range(len(pts) - 1):
            if cum[i] <= s <= cum[i + 1] + 1e-12:
                t = 0.0 if cum[i + 1] - cum[i] < 1e-12 else (s - cum[i]) / (cum[i + 1] - cum[i])
                return (pts[i][0] + t * (pts[i + 1][0] - pts[i][0]), pts[i][1] + t * (pts[i + 1][1] - pts[i][1]))
        return pts[-1]
    def in_tab(s):
        return any(a - 1e-9 <= s <= b + 1e-9 for a, b in spans)
    events = sorted(set(cuts) | set(cum))
    moves, z = [], z_cut
    for s in events[1:]:
        x, y = at(s)
        mid = (events[events.index(s) - 1] + s) / 2
        target = z_tab if in_tab(mid) else z_cut
        if abs(target - z) > 1e-9:                # a vertical move at the start of the tab / back down after it
            px, py = (moves[-1][1], moves[-1][2]) if moves else at(events[0])
            moves.append(("G1", px, py, target, plunge))
            z = target
        moves.append(("G1", x, y, z, None))
    return moves


def contour_moves(c: Contour, tool: Tool, job: CamJob) -> list:
    """All moves to cut one contour: rapid to the start, down, the passes, rapid up to the clearance height."""
    levels = _levels(job, tool)
    z_clear, z_app = job.clearance, job.approach
    x0, y0 = c.pts[0]
    moves = [("G0", x0, y0, z_clear, None), ("G0", x0, y0, z_app, None)]
    ring = c.pts
    total = _ring_length(ring)
    z_prev = 0.0
    if job.ramp and total > 1e-6:
        for z in levels:                          # a spiral down: each loop goes a bit deeper
            run = 0.0
            for a, b in zip(ring, ring[1:]):
                run += math.hypot(b[0] - a[0], b[1] - a[1])
                moves.append(("G1", b[0], b[1], z_prev + (z - z_prev) * run / total, tool.plunge))
            z_prev = z
        final_start = len(moves)
        flat = [("G1", b[0], b[1], levels[-1], tool.feed) for b in ring[1:]]      # one flat loop cleans the ramp
        n_tabs = 0
        if c.kind == "outer" and job.tabs > 0:
            n_tabs = min(job.tabs, int(total // (3 * max(job.tab_width, 1e-6))))
        z_tab = -job.thickness + job.tab_height
        if n_tabs > 0 and z_tab > levels[-1] + 1e-9:
            tabbed = _with_tabs(ring, levels[-1], z_tab, n_tabs, job.tab_width, tool.plunge)
            moves += [(m[0], m[1], m[2], m[3], tool.feed if m[4] is None else m[4]) for m in tabbed]
        else:
            moves += flat
    else:
        for i, z in enumerate(levels):
            moves.append(("G1", x0, y0, z, tool.plunge))                       # straight plunge at the start
            last = i == len(levels) - 1
            n_tabs = min(job.tabs, int(total // (3 * max(job.tab_width, 1e-6)))) if (last and c.kind == "outer" and job.tabs > 0) else 0
            z_tab = -job.thickness + job.tab_height
            if n_tabs > 0 and z_tab > z + 1e-9:
                tabbed = _with_tabs(ring, z, z_tab, n_tabs, job.tab_width, tool.plunge)
                moves += [(m[0], m[1], m[2], m[3], tool.feed if m[4] is None else m[4]) for m in tabbed]
            else:
                moves += [("G1", b[0], b[1], z, tool.feed) for b in ring[1:]]
    xe, ye = moves[-1][1], moves[-1][2]
    moves.append(("G0", xe, ye, z_clear, None))
    return moves


def estimate_minutes(moves_by_tool: list, start=(0.0, 0.0, 0.0)) -> float:
    """Cutting time from the moves: every feed move at its feed, every rapid at the machine's rapid speed."""
    t = 0.0
    x, y, z = start
    for _tool, moves in moves_by_tool:
        for code, mx, my, mz, f in moves:
            d = math.sqrt((mx - x) ** 2 + (my - y) ** 2 + (mz - z) ** 2)
            if code == "G1" and f:
                t += d / f
            else:
                t += d / (machine.RAPID_Z if abs(mz - z) > 1e-9 and abs(mx - x) + abs(my - y) < 1e-9 else machine.RAPID_XY)
            x, y, z = mx, my, mz
    return t


def groups(plan: Plan, job: CamJob, tools: dict) -> list:
    """[(Tool, [moves])] in cutting order: one entry per tool (the inner and the outer tool may be the same)."""
    out = []
    for c in plan.contours:
        tool = tools[c.tool]
        mv = contour_moves(c, tool, job)
        if out and out[-1][0].number == tool.number:
            out[-1][1].extend(mv)
        else:
            out.append((tool, mv))
    return out
