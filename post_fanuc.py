"""G-code for the router, written the way the post "Mariani - CR Onsrud 336G18CW7" (FANUC, SolidCAM) writes it
(see post/*.gpp): the same start and end of a program, the same tool change / spindle / coolant / smoothing blocks and
the same non-modal "G1 X.. Y.. Z.." lines. The post itself cannot be run here, so this follows its text."""
from __future__ import annotations

import datetime

import machine_profile as machine


def num(v: float) -> str:
    """A coordinate the way the post prints it: up to 3 decimals, no trailing zeros, always a decimal point ("12.", "-3.5")."""
    s = f"{round(v, 3):.3f}".rstrip("0")
    if s in ("-0.", "-0"):
        s = "0."
    return s if s.endswith(".") or "." in s else s + "."


def _comment(text: str) -> str:
    """A block comment: no parentheses inside, plain ASCII."""
    t = "".join(ch if 32 <= ord(ch) < 127 else "?" for ch in str(text)).replace("(", "[").replace(")", "]")
    return f"({t})"


class _Writer:
    def __init__(self):
        self.lines: list[str] = []
        self.pos = {"X": None, "Y": None, "Z": None}
        self.feed = None
        self.coolant_on = None

    def block(self, text: str):
        self.lines.append(text)

    def safe_position(self):                      # @usr_SafePosition(0)
        self.block("G0 G28 G91 Z0.")
        self.block("G90")
        self.pos["Z"] = None

    def move(self, code, x, y, z, f):
        parts = [code]
        for ax, v in (("X", x), ("Y", y), ("Z", z)):
            if self.pos[ax] is None or abs(self.pos[ax] - v) > 1e-9 or (code == "G0" and ax == "Z"):
                parts.append(ax + num(v))
                self.pos[ax] = v
        if len(parts) == 1:
            return                                # nothing to move
        if code == "G1" and f is not None and (self.feed is None or abs(self.feed - f) > 1e-9):
            parts.append("F" + num(f))
            self.feed = f
        self.block(" ".join(parts))


def program(name: str, groups: list, job, sheet_no: int, sheet_size: tuple, project: str = "", run_no=None,
            stats: dict | None = None, z0_note: str | None = None, spin: str = "M3", operation: str | None = None,
            brushes: bool = True, solidcam: bool = False) -> str:
    """The complete program text for one sheet. groups: [(Tool, [moves])] from cam.groups().
    solidcam: the program exactly as the SolidCAM post writes it (header boxes, N blocks, a space at the end of the blocks, CR LF)."""
    w = _Writer()
    now = datetime.datetime.now()
    w.block("%")
    w.block(f"<{name}>")
    if solidcam:
        _solidcam_header(w, name, groups, operation, stats, now)
    else:
        w.block(_comment("------------------------------"))
        w.block(_comment(f"PROGRAM: {name}"))
        if project:
            w.block(_comment(f"PROJECT: {project}" + (f"  RUN: {run_no}" if run_no else "")))
        w.block(_comment(f"SHEET {sheet_no}: {sheet_size[0]:g} X {sheet_size[1]:g} MM, THICKNESS {job.thickness:g} MM"))
        w.block(_comment(f"MACHINE: {machine.NAME}  CONTROL: {machine.CONTROLLER}"))
        w.block(_comment(f"POST: after {machine.NAME} V{machine.POST_VERSION} (Nesto CAM)"))
        if job.programmer:
            w.block(_comment(f"PROGRAMMED BY: {job.programmer}"))
        w.block(_comment(f"DATE: {now.strftime('%Y-%m-%d %H:%M')}"))
        w.block(_comment(z0_note or f"WORK OFFSET G{machine.WORK_OFFSET}: ZERO AT THE LOWER LEFT CORNER OF THE SHEET, Z0 = TOP OF THE MATERIAL"))
        if stats:
            w.block(_comment(f"CUT LENGTH {stats.get('length', 0):.0f} MM, ESTIMATED TIME {stats.get('minutes', 0):.1f} MIN"))
        w.block(_comment("TOOL LIST"))
        for tool, _ in groups:
            w.block(_comment(f"T{tool.number}  D{tool.diameter:g}  {tool.name or 'ENDMILL'}  S{tool.rpm:g}  F{tool.feed:g}"))
        w.block(_comment("------------------------------"))
    w.block("G90 G21 G40 G49 G80 G94 G98 G17")      # @start_program
    w.block(_comment("METRIC PROGRAM"))
    w.safe_position()
    first = True
    brush_down = False
    for job_no, (tool, moves) in enumerate(groups, 1):
        if solidcam:
            _solidcam_operation(w, operation or "CONTOUR", tool)
            w.block(f"N{job_no}")
        else:
            w.block(_comment(f"OPERATION: {operation}" if operation else f"OPERATION: CONTOURS WITH T{tool.number} {tool.name or 'ENDMILL'}"))
        if not first:
            w.block("M1")                           # @start_of_job: optional stop before a tool change
            w.block("M9") if w.coolant_on else None
            w.coolant_on = None
            w.safe_position()
        # @rapid_move of the first move: work offset, tool call, C axis, spindle, first position, smoothing, coolant, length offset
        w.block(f"G{machine.WORK_OFFSET}")
        w.block(f"M6 T{tool.number}")
        w.block("G0 C0.")
        w.block(f"S{num(tool.rpm).rstrip('.')} {spin}")
        w.pos = {"X": None, "Y": None, "Z": None}
        w.feed = None
        first_move = moves[0]
        if solidcam:
            w.block("G94 G0 X" + num(first_move[1]) + " Y" + num(first_move[2]))
        else:
            w.block("G94")
            w.block("G0 X" + num(first_move[1]) + " Y" + num(first_move[2]))
        w.pos["X"], w.pos["Y"] = first_move[1], first_move[2]
        if job.smoothing:
            w.block(f"G05.1 Q1 R{num(job.smoothing).rstrip('.')} (AICC/AIAPC HIGH SPEED MODE ON)")
        if tool.coolant != "none":
            w.block("M8" if tool.coolant == "flood" else "M14")
            w.coolant_on = tool.coolant
        w.block(f"G0 G43 H{tool.number} Z" + num(first_move[3]))
        w.pos["Z"] = first_move[3]
        for code, x, y, z, f in moves[1:]:
            w.move(code, x, y, z, f)
            if brushes and code == "G1" and not brush_down:          # @line of the post: brushes down after the first cutting move
                w.block("M96 (BRUSHES DOWN)")
                brush_down = True
        # @end_of_job
        if job.smoothing:
            w.block("G05.1 Q0 (AICC/AIAPC HIGH SPEED MODE OFF)")
        w.safe_position()
        if brush_down:
            w.block("M97 (BRUSHES UP)")
        first = False
    if w.coolant_on:                                 # @end_program
        w.block("M9")
    w.block("M5")
    w.safe_position()
    w.block("G0 C0.")
    w.block("M30")
    w.block("%")
    if solidcam:
        return "\r\n".join(_solidcam_line(x) for x in w.lines)       # CR LF, nothing after the last %
    return "\n".join(w.lines) + "\n"


# ------------------------------------------------------------------------------------------------ the SolidCAM text
_BOX = 48                                                         # width of the comment boxes


def _box(text: str, center: bool = False) -> str:
    if center:
        pad = _BOX - len(text)
        return "(" + " " * (pad - pad // 2) + text + " " * (pad // 2) + ")"
    return "(" + text.ljust(_BOX) + ")"


_RULE = "(" + "-" * _BOX + ")"


def _solidcam_line(line: str) -> str:
    """A block ends with a space, like the post writes it; comment lines, program name, the first G90 line, G90 and % do not."""
    if line.startswith(("(", "%", "<", "G90 G21")) or "(" in line or line == "G90":
        return line
    return line + " "


def _hms(minutes: float) -> str:
    s = int(round(minutes * 60))
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def _tool_desc(tool) -> str:
    return f"{tool.name or 'END MILL'} {int(tool.flutes)}F"


def _solidcam_header(w, name: str, groups: list, operation, stats, now) -> None:
    short = machine.NAME.split(" - ")[-1].upper()
    base = name.rsplit(".", 1)[0].upper()
    for line in (_RULE, _box("POST PROCESSOR INFORMATION", True), _RULE,
                 _box("S-CAM VERSION  : NESTOCAM"), _box("LICENSED TO    : MARIANI"), _box(f"MACHINE        : {short}"),
                 _box(f"CONTROLLER     : {machine.CONTROLLER}"), _box(f"POST VERSION   : {machine.POST_VERSION}"),
                 _box("POST CREATED BY: SSG"), _RULE, _box("PROGRAM INFORMATION", True), _RULE,
                 _box(f"PART NAME   : {base}"), _box(f"G-CODE DATE : {now.strftime('%d-%b-%Y').upper()}"),
                 _box(f"G-CODE TIME : {now.strftime('%H:%M:%S')}"), _box(f"EST M/C TIME: {_hms((stats or {}).get('minutes', 0.0))}"),
                 _RULE, _box("TOOL LIST", True), _RULE, "(TOOL|              TOOL              |   TOOL   )",
                 "( NO |           DESCRIPTION          |  LENGTH  )", _RULE):
        w.block(line)
    seen = set()
    for tool, _ in groups:
        if tool.number in seen:
            continue
        seen.add(tool.number)
        txt = f"{int(round(tool.length))}mm"
        pad = 10 - len(txt)
        w.block(f"({str(tool.number).rjust(3)} | {_tool_desc(tool).ljust(31)}|{' ' * (pad // 2)}{txt}{' ' * (pad - pad // 2)})")
    w.block(_RULE)


def _solidcam_operation(w, operation: str, tool) -> None:
    for line in (_RULE, _box("OPERATION INFORMATION", True), _RULE, _box(operation), _RULE, _RULE, _box("TOOL INFORMATION", True), _RULE,
                 _box(_tool_desc(tool)), _box(f"TOOL LENGTH: {tool.length:.2f}mm")):
        w.block(line)
    if tool.holder:
        w.block(_box(f"HOLDER: {tool.holder.upper()}"))
    w.block(_RULE)
