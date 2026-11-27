"""NestoCAM step 5 - the G-code from the information of the four steps:

  1 Geometry  the bottom outlines and holes of the parts of a sheet (Z = 0 is the bottom of the parts, the work zero is the lower
              left corner of the sheet),
  2 Tools     the chosen tool (diameter ...),
  3 Data      feed XY / Z, spin rate and direction,
  4 Levels    clearance level (rapid moves), safety distance (feed Z starts at upper level + safety), upper level, profile depth
              (the tool goes down to upper level - profile depth).

The contours and their order come from cam.plan_sheet (holes first, then the outlines; the path of the tool centre is offset in
software, so there is no G41 / G42) and the text from post_fanuc.program (the Mariani post). Everything the four steps do NOT say
is listed in ASSUMPTIONS, so it is easy to see what is still missing when the result is compared with the SolidCAM G-code.
"""
from __future__ import annotations


import math

import cam
import machine_profile as machine
import post_fanuc

DRILL_TOLERANCE = 0.05     # mm: a round hole is drilled when its diameter is this close to the diameter of a drill
ARC_TOLERANCE = 0.01       # mm: the largest distance of a straight piece from the true arc (as SolidCAM: a quarter circle of R6.35 has 14 pieces)

ASSUMPTIONS = [
    "Depth: ONE pass from the upper level down to (upper level - profile depth). There is no step-down in the steps.",
    "Entry and exit: none. The tool goes down straight at the feed Z, at the start point of the contour, and the contour is a "
    "closed loop that ends where it started; there are no lead in / lead out moves.",
    "Start point: the lowest left vertex of the part (the lowest vertex, the leftmost of those): the contour starts where the "
    "tool path of the edge that leaves this vertex begins.",
    "Operations: the holes and the outlines are two operations (named <operation>-HOLES and <operation>-CONTOUR in the operation box), each with "
    "its own boxes, N block and end sequence (G05.1 Q0, safe position, M97); the tool change M6 and M96 are written once.",
    "Round holes: a hole that is a circle is cut as a true circle (not as the polygon it is stored as), starting at its top.",
    "Order: the order of the contours that was set in step 1 (by default the holes first, then the outlines, each from the lower left "
    "corner of the part, from left to right).",
    "Cutting side: holes are cut from inside, outlines from outside (the tool centre path is offset by the tool radius in software, "
    "so there is no cutter compensation G41 / G42). Always climb milling for the spin direction.",
    f"Arcs: straight pieces of equal length, at most {ARC_TOLERANCE:g} mm away from the true arc, like SolidCAM (a quarter circle of R6.35 is 14 pieces).",
    "Coolant: none. No tabs. No finishing pass and no stock to leave.",
    f"Machine: work offset G{machine.WORK_OFFSET}, smoothing G05.1 Q1 R{machine.DEFAULT_SMOOTHING:g}, rapid at the clearance level, "
    "brushes down after the first cutting move (M96) and up at the end (M97), safe position (G28) like the post.",
]


def tool_from_row(t: dict, data: dict) -> cam.Tool:
    """The cam.Tool the planner and the post work with: the geometry of the tool from step 2, the feeds and speed from step 3."""
    return cam.Tool(number=int(t["number"]), name=t.get("name", "") or "", diameter=float(t["diameter"]), flutes=int(t.get("flutes", 2)),
                    rpm=float(data["rpm"]), feed=float(data["feed_xy"]), plunge=float(data["feed_z"]), step_down=1e9, coolant="none",
                    length=float(t.get("outside_holder") or 0.0), holder="")                       # the tool records have no holder: no HOLDER line in the header


def contour_moves(c: cam.Contour, tool: cam.Tool, data: dict, lv: dict) -> list:
    """Moves (code, x, y, z, feed) of one contour: rapid at the clearance level, rapid down to upper level + safety distance, feed Z
    down to the bottom of the cut, the closed contour at the feed XY, rapid up to the clearance level. No lead in / out."""
    z_clear = lv["clearance"]
    z_feed = lv["upper"] + lv["safety"]
    z_bot = lv["upper"] - lv["depth"]
    ring = c.pts
    x0, y0 = ring[0]
    moves = [("G0", x0, y0, z_clear, None), ("G0", x0, y0, z_feed, None), ("G1", x0, y0, z_bot, data["feed_z"])]
    moves += [("G1", b[0], b[1], z_bot, data["feed_xy"]) for b in ring[1:]]
    moves.append(("G0", ring[-1][0], ring[-1][1], z_clear, None))
    return moves


def problems(t: dict | None, data: dict, lv: dict) -> list[str]:
    """What stops the G-code from being made (empty list = it can be made)."""
    out = []
    if t is None:
        out.append("Choose a tool in step 2 (tick it and press Use for CAM).")
        return out
    from data_ui import validate as v_data
    from levels_ui import validate as v_lv
    out += v_data(data) + v_lv(lv)
    out += cam.Tool(number=int(t["number"]), diameter=float(t["diameter"]), rpm=float(data["rpm"]), feed=float(data["feed_xy"]),
                    plunge=float(data["feed_z"])).problems()
    return out


def warnings(t: dict, lv: dict, drilling: bool = False) -> list[str]:
    w = []
    if drilling:
        if str(t.get("tool_type") or "").upper() != "DRILL":
            w.append(f"T{int(t['number'])} is not a drill: this operation drills holes, choose a drill in step 2.")
    elif str(t.get("tool_type") or "END MILL").upper() != "END MILL":
        w.append(f"T{int(t['number'])} is a {str(t.get('tool_type')).lower()}. The G-code of this step mills the holes and outlines "
                 "along their contours, which needs an end mill: choose an end mill in step 2.")
    if lv["depth"] > float(t["cutting_length"]) + 1e-9:
        w.append(f"The profile depth ({lv['depth']:g}) is more than the cutting length of the tool ({float(t['cutting_length']):g}).")
    if lv["depth"] > lv["upper"] + 1e-9:
        w.append(f"The tool goes below Z = 0 (down to {lv['upper'] - lv['depth']:g}), the bottom of the parts.")
    return w


def drill_contours(sheet: dict, ids, tool: cam.Tool, order_ids: list | None = None) -> list:
    """The holes of an operation that drills, as contours (the ring of the hole, for the picture and the counts), in drilling order:
    the order that was set (order_ids), else from left to right. Only round holes can be drilled."""
    by_id = {c["id"]: c for c in cam.scan_contours(sheet) if c["kind"] == "inner" and c["circle"]}
    wanted = [i for i in (ids or []) if i in by_id]
    rank = {cid: n for n, cid in enumerate(order_ids or [])}
    wanted.sort(key=lambda i: (rank.get(i, len(rank)), by_id[i]["key"]))
    out = []
    for cid in wanted:
        c = by_id[cid]
        cx, cy, _r = c["circle"]
        r = tool.diameter / 2
        ring = [(cx + r * math.cos(2 * math.pi * k / 48), cy + r * math.sin(2 * math.pi * k / 48)) for k in range(48)]
        out.append((cam.Contour("inner", c["part"], ring + [ring[0]], tool.number, c["ring"], r, c["key"]), (cx, cy)))
    return out


def drill_moves(holes: list, tool: cam.Tool, data: dict, lv: dict, extra: float = 0.0, peck: float = 0.0) -> list:
    """The moves of a drilling operation: rapid to the first hole at the clearance level, then one canned cycle per hole (G81, or the
    peck drilling G83 when a peck depth is given): R plane = upper level + safety distance, bottom = upper level - profile depth - extra."""
    if not holes:
        return []
    code = "G83" if peck > 0 else "G81"
    r_plane = lv["upper"] + lv["safety"]
    z_bot = lv["upper"] - lv["depth"] - extra
    x0, y0 = holes[0][1]
    return [("G0", x0, y0, lv["clearance"], None)] + [(code, cx, cy, z_bot, data["feed_z"], r_plane, peck) for _c, (cx, cy) in holes]


def build(sheet: dict, t: dict, data: dict, lv: dict, thickness: float, margin: float = 0.0, spacing: float = 0.0,
          order_ids: list | None = None, contours: list | None = None, drill: dict | None = None):
    """(plan, groups, tool, job) for one sheet; groups as post_fanuc.program wants them.
    contours: only these contours (contour ids) are cut (None = all). drill: {"extra", "peck"} makes it a drilling operation: the
    contours are round holes that are drilled with canned cycles."""
    tool = tool_from_row(t, data)
    job = cam.CamJob(thickness=thickness, inner_tool=tool.number, outer_tool=tool.number, climb=(data["direction"] == "CW"))
    if drill is not None:
        holes = drill_contours(sheet, contours, tool, order_ids)
        plan = cam.Plan(contours=[c for c, _p in holes])
        if len(holes) < len(contours or []):
            plan.warnings.append(f"{len(contours) - len(holes)} hole(s) of this operation are not (round) holes on this sheet any more.")
        moves = drill_moves(holes, tool, data, lv, float(drill.get("extra") or 0.0), float(drill.get("peck") or 0.0))
        return plan, ([(tool, moves)] if moves else []), tool, job
    plan = cam.plan_sheet(sheet, job, {tool.number: tool}, margin, spacing, start="lowleft", order="left_right", arc_tol=ARC_TOLERANCE,
                          custom_order=order_ids)
    if contours is not None:                                  # an operation of a few contours: the warnings of the others do not count
        wanted = set(contours)
        plan.contours = [c for c in plan.contours if cam.contour_id(c.kind, c.key) in wanted]
        kinds = {c.kind for c in plan.contours}
        plan.warnings = [w for w in plan.warnings
                         if not (("outline(s)" in w or "edge margin" in w) and "outer" not in kinds)
                         and not ("hole(s) are smaller" in w and "inner" not in kinds)]
        if len(plan.contours) < len(wanted):
            plan.warnings.append(f"{len(wanted) - len(plan.contours)} contour(s) of this operation are not on this sheet any more or are "
                                 "too small for the tool.")
    groups, kind = [], None                       # an operation for every run of holes or of outlines (<operation>-HOLES, <operation>-CONTOUR)
    for c in plan.contours:
        if c.kind != kind:
            groups.append((tool, []))
            kind = c.kind
        groups[-1][1].extend(contour_moves(c, tool, data, lv))
    return plan, groups, tool, job


def operation_names(plan: cam.Plan, operation: str | None = None) -> list[str]:
    """The names of the operations in the program, one for every run of holes or of outlines: the name of the operation, a dash, and
    HOLES or CONTOUR (Hole test-HOLES, Hole test-CONTOUR; a name that comes again gets a number: ...-HOLES 2), so that whoever reads
    the program sees which part of it cuts what."""
    names, kind, seen = [], None, {}
    for c in plan.contours:
        if c.kind != kind:
            kind = c.kind
            base = "HOLES" if kind == "inner" else "CONTOUR"
            seen[base] = seen.get(base, 0) + 1
            label = base if seen[base] == 1 else f"{base} {seen[base]}"
            names.append(f"{operation}-{label}" if operation else label)
    return names


def text(name: str, sheet: dict, sheet_no: int, t: dict, data: dict, lv: dict, thickness: float, project: str = "", run_no=None,
         margin: float = 0.0, spacing: float = 0.0, operation: str | None = None, order_ids: list | None = None,
         contours: list | None = None, drill: dict | None = None) -> str:
    plan, groups, _tool, job = build(sheet, t, data, lv, thickness, margin, spacing, order_ids, contours, drill)
    length = sum(cam._ring_length(c.pts) for c in plan.contours)
    minutes = cam.estimate_minutes(groups) if groups else 0.0
    note = (f"WORK OFFSET G{machine.WORK_OFFSET}: ZERO AT THE LOWER LEFT CORNER OF THE SHEET, Z0 = BOTTOM OF THE PARTS, "
            f"TOP OF THE MATERIAL Z{lv['upper']:g}")
    if contours is not None or drill is not None:             # an operation of its own (AutoCam): its name is the operation box
        names = [operation or "OPERATION"] + [f"{operation or 'OPERATION'}{i}" for i in range(1, len(groups))]
    else:
        names = operation_names(plan, operation)
    return post_fanuc.program(name, groups, job, sheet_no, (sheet["width"], sheet["height"]), project, run_no,
                              {"length": length, "minutes": minutes}, z0_note=note, spin="M3" if data["direction"] == "CW" else "M4", operation=operation, solidcam=True,
                              op_names=names)


def sheet_program(name: str, sheet: dict, sheet_no: int, ops: list, thickness: float, project: str = "", run_no=None,
                  margin: float = 0.0, spacing: float = 0.0):
    """ONE program for a sheet from several operations (AutoCam): ops = [{"name", "tool" (a tool row), "data", "lv", "contours",
    "drill" ({"extra", "peck"} or None), "order"}], in cutting order. Every operation has its own boxes and N block; a tool change
    is written where the tool is not the one of the operation before. Returns (text, plans)."""
    groups, names, plans, length = [], [], [], 0.0
    job = None
    for op in ops:
        plan, grp, _tool, j = build(sheet, op["tool"], op["data"], op["lv"], thickness, margin, spacing, op.get("order"), op["contours"], op.get("drill"))
        job = job or j
        plans.append(plan)
        length += sum(cam._ring_length(c.pts) for c in plan.contours)
        for k, g_ in enumerate(grp):
            groups.append(g_)
            names.append(op["name"] if k == 0 else f"{op['name']}{k}")
    if not groups:
        return "", plans
    first_lv, first_data = ops[0]["lv"], ops[0]["data"]
    minutes = cam.estimate_minutes(groups)
    note = (f"WORK OFFSET G{machine.WORK_OFFSET}: ZERO AT THE LOWER LEFT CORNER OF THE SHEET, Z0 = BOTTOM OF THE PARTS, "
            f"TOP OF THE MATERIAL Z{first_lv['upper']:g}")
    return post_fanuc.program(name, groups, job, sheet_no, (sheet["width"], sheet["height"]), project, run_no,
                              {"length": length, "minutes": minutes}, z0_note=note, spin="M3" if first_data["direction"] == "CW" else "M4",
                              operation=ops[0]["name"], solidcam=True, op_names=names), plans
