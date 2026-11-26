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


import cam
import machine_profile as machine
import post_fanuc

ARC_TOLERANCE = 0.02       # mm: the largest distance of a straight piece from the true arc

ASSUMPTIONS = [
    "Depth: ONE pass from the upper level down to (upper level - profile depth). There is no step-down in the steps.",
    "Entry and exit: none. The tool goes down straight at the feed Z, at the start point of the contour, and the contour is a "
    "closed loop that ends where it started; there are no lead in / lead out moves.",
    "Start point: the lowest left vertex of the part (the lowest vertex, the leftmost of those): the contour starts where the "
    "tool path of the edge that leaves this vertex begins.",
    "Order: the holes first, then the outlines, each from the lower left corner of the part, from left to right.",
    "Cutting side: holes are cut from inside, outlines from outside (the tool centre path is offset by the tool radius in software, "
    "so there is no cutter compensation G41 / G42). Always climb milling for the spin direction.",
    f"Arcs: straight lines, at most {ARC_TOLERANCE:g} mm away from the true arc.",
    "Coolant: none. No tabs. No finishing pass and no stock to leave.",
    f"Machine: work offset G{machine.WORK_OFFSET}, smoothing G05.1 Q1 R{machine.DEFAULT_SMOOTHING:g}, rapid at the clearance level, "
    "brushes down after the first cutting move (M96) and up at the end (M97), safe position (G28) like the post.",
]


def tool_from_row(t: dict, data: dict) -> cam.Tool:
    """The cam.Tool the planner and the post work with: the geometry of the tool from step 2, the feeds and speed from step 3."""
    return cam.Tool(number=int(t["number"]), name=t.get("name", "") or "", diameter=float(t["diameter"]), flutes=int(t.get("flutes", 2)),
                    rpm=float(data["rpm"]), feed=float(data["feed_xy"]), plunge=float(data["feed_z"]), step_down=1e9, coolant="none")


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


def warnings(t: dict, lv: dict) -> list[str]:
    w = []
    if lv["depth"] > float(t["cutting_length"]) + 1e-9:
        w.append(f"The profile depth ({lv['depth']:g}) is more than the cutting length of the tool ({float(t['cutting_length']):g}).")
    if lv["depth"] > lv["upper"] + 1e-9:
        w.append(f"The tool goes below Z = 0 (down to {lv['upper'] - lv['depth']:g}), the bottom of the parts.")
    return w


def build(sheet: dict, t: dict, data: dict, lv: dict, thickness: float, margin: float = 0.0, spacing: float = 0.0):
    """(plan, groups, tool) for one sheet; groups as post_fanuc.program wants them."""
    tool = tool_from_row(t, data)
    job = cam.CamJob(thickness=thickness, inner_tool=tool.number, outer_tool=tool.number, climb=(data["direction"] == "CW"))
    plan = cam.plan_sheet(sheet, job, {tool.number: tool}, margin, spacing, start="lowleft", order="left_right", arc_tol=ARC_TOLERANCE)
    moves = []
    for c in plan.contours:
        moves += contour_moves(c, tool, data, lv)
    return plan, ([(tool, moves)] if moves else []), tool, job


def text(name: str, sheet: dict, sheet_no: int, t: dict, data: dict, lv: dict, thickness: float, project: str = "", run_no=None,
         margin: float = 0.0, spacing: float = 0.0, operation: str | None = None) -> str:
    plan, groups, _tool, job = build(sheet, t, data, lv, thickness, margin, spacing)
    length = sum(cam._ring_length(c.pts) for c in plan.contours)
    minutes = cam.estimate_minutes(groups) if groups else 0.0
    note = (f"WORK OFFSET G{machine.WORK_OFFSET}: ZERO AT THE LOWER LEFT CORNER OF THE SHEET, Z0 = BOTTOM OF THE PARTS, "
            f"TOP OF THE MATERIAL Z{lv['upper']:g}")
    return post_fanuc.program(name, groups, job, sheet_no, (sheet["width"], sheet["height"]), project, run_no,
                              {"length": length, "minutes": minutes}, z0_note=note, spin="M3" if data["direction"] == "CW" else "M4", operation=operation)
