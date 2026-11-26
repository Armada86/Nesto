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
            stats: dict | None = None, z0_note: str | None = None, spin: str = "M3") -> str:
    """The complete program text for one sheet. groups: [(Tool, [moves])] from cam.groups()."""
    w = _Writer()
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    w.block("%")
    w.block(f"<{name}>")
    w.block(_comment("------------------------------"))
    w.block(_comment(f"PROGRAM: {name}"))
    if project:
        w.block(_comment(f"PROJECT: {project}" + (f"  RUN: {run_no}" if run_no else "")))
    w.block(_comment(f"SHEET {sheet_no}: {sheet_size[0]:g} X {sheet_size[1]:g} MM, THICKNESS {job.thickness:g} MM"))
    w.block(_comment(f"MACHINE: {machine.NAME}  CONTROL: {machine.CONTROLLER}"))
    w.block(_comment(f"POST: after {machine.NAME} V{machine.POST_VERSION} (Nesto CAM)"))
    if job.programmer:
        w.block(_comment(f"PROGRAMMED BY: {job.programmer}"))
    w.block(_comment(f"DATE: {now}"))
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
    for tool, moves in groups:
        w.block(_comment(f"OPERATION: CONTOURS WITH T{tool.number} {tool.name or 'ENDMILL'}"))
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
        w.block("G94")
        w.pos = {"X": None, "Y": None, "Z": None}
        w.feed = None
        first_move = moves[0]
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
        # @end_of_job
        if job.smoothing:
            w.block("G05.1 Q0 (AICC/AIAPC HIGH SPEED MODE OFF)")
        w.safe_position()
        first = False
    if w.coolant_on:                                 # @end_program
        w.block("M9")
    w.block("M5")
    w.safe_position()
    w.block("G0 C0.")
    w.block("M30")
    w.block("%")
    return "\n".join(w.lines) + "\n"
