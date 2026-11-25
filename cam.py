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


def _tool_for(kind: str, job: CamJob, tools: dict):
    return tools.get(job.inner_tool if kind == "inner" else job.outer_tool)


def plan_sheet(sheet: dict, job: CamJob, tools: dict, margin: float = 0.0, spacing: float = 0.0) -> Plan:
    """The contours of a sheet (a stored layout dict) in cutting order. `tools`: {number: Tool}."""
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
            ring = Polygon(poly.exterior).buffer(r, quad_segs=10).simplify(0.005).exterior
            pts = [tuple(c) for c in ring.coords]
            # the tool must not touch the neighbouring parts: they have to be at least a tool diameter apart
            if others:
                line = LineString(pts)
                if min(line.distance(o) for o in others) < r - 0.02:
                    too_close += 1
                    gouge_names.append(name)
            outer.append(Contour("outer", name, pts, job.outer_tool))
            for hole in poly.interiors:
                ri = inner_tool.diameter / 2
                shrunk = Polygon(hole).buffer(-ri, quad_segs=10)
                if shrunk.is_empty:
                    too_small.append(name)
                    plan.skipped += 1
                    continue
                for sp in ([shrunk] if shrunk.geom_type == "Polygon" else list(shrunk.geoms)):
                    inner.append(Contour("inner", name, [tuple(c) for c in sp.simplify(0.005).exterior.coords], job.inner_tool))
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
    # cutting order: nearest neighbour, the inner contours of all parts first, then the outlines
    pos = (0.0, 0.0)
    ordered = []
    for group, ccw_for_climb in ((inner, True), (outer, False)):
        todo = list(group)
        while todo:
            best = min(todo, key=lambda c: min(math.hypot(x - pos[0], y - pos[1]) for x, y in c.pts))
            todo.remove(best)
            ring = _orient(best.pts, ccw_for_climb if job.climb else not ccw_for_climb)
            ring = _start_near(ring, pos)
            best = Contour(best.kind, best.part, ring, best.tool)
            ordered.append(best)
            pos = ring[0]
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
