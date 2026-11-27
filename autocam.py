"""AutoCam: from the holes and the outlines of one sheet to the operations that cut them, without anybody choosing anything.

The rules (all of them are written in the report that AutoCam shows):
  * a ROUND hole whose diameter is within DRILL_TOLERANCE of a drill of the library is DRILLED with that drill (canned cycle);
  * every other hole is MILLED along its contour with the largest end mill of the library that fits inside it;
  * the OUTLINES are milled with one end mill: the largest one that does not touch the neighbouring parts (the parts have to be at least
    a tool diameter apart) and fits the edge margin;
  * the cutting order: the drilled holes (one operation per drill), the milled holes (one operation per end mill), then the outlines, so
    a part is still held by the sheet while its holes are cut; inside an operation from the lower left corner of the part, left to right;
  * the cutting data, the levels and the material thickness come from the CAM options (cutting data) and the thickness of the run.
This module has no screen: it makes the operations (a name and settings, as the operations of the NestoCAM tab have them) and the program.
"""
from __future__ import annotations

import cam
import gcode_cam

DEFAULTS = {
    "mill": {"feed_xy": 2500.0, "feed_z": 100.0, "rpm": 10000.0, "direction": "CW"},
    "drill": {"feed": 150.0, "rpm": 6000.0, "peck": 0.0, "extra": 0.0},
    "levels": {"clearance": 45.0, "safety": 2.0},
}


def merged_defaults(saved: dict | None) -> dict:
    """The cutting data defaults: what is saved in the CAM options over the built-in values."""
    out = {k: dict(v) for k, v in DEFAULTS.items()}
    for group, vals in (saved or {}).items():
        if group in out and isinstance(vals, dict):
            out[group].update({k: v for k, v in vals.items() if k in out[group] and v is not None})
    return out


def _tool(row: dict, data: dict) -> cam.Tool:
    return gcode_cam.tool_from_row(row, data)


def _fits(ring, radius: float) -> bool:
    from shapely.geometry import Polygon
    return not Polygon(ring).buffer(-radius).is_empty


def outline_tool(sheet: dict, mills: list, margin: float, spacing: float):
    """(tool row, warnings): the largest end mill that does not touch the neighbours of any part and fits the edge margin; when none does,
    the smallest end mill and what is wrong."""
    probe = {"feed_xy": 1000.0, "feed_z": 100.0, "rpm": 10000.0, "direction": "CW"}
    last = None
    for row in sorted(mills, key=lambda r: -r["diameter"]):
        t = _tool(row, probe)
        job = cam.CamJob(thickness=18.0, inner_tool=t.number, outer_tool=t.number)
        plan = cam.plan_sheet(sheet, job, {t.number: t}, margin, spacing, start="lowleft", order="left_right", arc_tol=gcode_cam.ARC_TOLERANCE)
        bad = [w for w in plan.warnings if "closer than the tool diameter" in w or "edge margin" in w]
        last = (row, bad)
        if not bad:
            return row, []
    return last


def levels_for(defaults: dict) -> dict:
    """The levels of an operation as the operations store them: the upper level and the profile depth follow the thickness (None)."""
    lv = defaults["levels"]
    return {"clearance": float(lv["clearance"]), "safety": float(lv["safety"]), "upper": None, "depth": None}


def plan(sheet: dict, sheet_no: int, tools: list, defaults: dict | None, thickness: float, margin: float = 0.0, spacing: float = 0.0) -> dict:
    """The operations for one sheet. tools: the tool library (rows with tool_type, number, diameter ...).
    Returns {"operations": [{"name", "kind" ("drill" | "hole" | "outline"), "tool" (row), "contours" (ids), "settings" (for the database),
    "data", "lv" (the levels with the thickness filled in), "drill"}], "warnings": [...], "skipped": [...], "problems": [...]}"""
    d = merged_defaults(defaults)
    mills = [t for t in tools if str(t.get("tool_type", "END MILL")).upper() == "END MILL"]
    drills = [t for t in tools if str(t.get("tool_type", "")).upper() == "DRILL"]
    out = {"operations": [], "warnings": [], "skipped": [], "problems": []}
    items = cam.scan_contours(sheet)
    if not items:
        out["problems"].append("There is nothing to cut on this sheet.")
        return out
    if not mills:
        out["problems"].append("There is no end mill in the tool library: create one in CAM options (Tools).")
        return out
    holes = sorted((c for c in items if c["kind"] == "inner"), key=lambda c: c["key"])
    outlines = sorted((c for c in items if c["kind"] == "outer"), key=lambda c: c["key"])
    drilled: dict[int, list] = {}
    milled: dict[int, list] = {}
    for h in holes:
        if h["circle"] and drills:
            diam = 2 * h["circle"][2]
            best = min(drills, key=lambda t: abs(t["diameter"] - diam))
            if abs(best["diameter"] - diam) <= gcode_cam.DRILL_TOLERANCE:
                drilled.setdefault(best["number"], []).append(h["id"])
                continue
        fit = next((t for t in sorted(mills, key=lambda r: -r["diameter"]) if _fits(h["ring"], t["diameter"] / 2)), None)
        if fit is None:
            out["skipped"].append(f"{h['part']}: the hole at X{h['key'][0]:g} Y{h['key'][1]:g} is too small for every end mill: not cut.")
            continue
        milled.setdefault(fit["number"], []).append(h["id"])
    by_no = {t["number"]: t for t in tools}
    lv_store = levels_for(d)
    lv = {**lv_store, "upper": thickness, "depth": thickness}
    mdata = dict(d["mill"])

    def _op(kind, row, ids):
        if kind == "drill":
            data = {"feed_xy": mdata["feed_xy"], "feed_z": d["drill"]["feed"], "rpm": d["drill"]["rpm"], "direction": mdata["direction"]}
            drill = {"extra": d["drill"]["extra"], "peck": d["drill"]["peck"]}
            label = "DRILL"
        else:
            data, drill = dict(mdata), None
            label = "HOLES" if kind == "hole" else "CONTOUR"
        name = f"Sheet{sheet_no}-{label}-{row['diameter']:g}"
        settings = {"sheet": sheet_no, "thickness": float(thickness), "tool": int(row["number"]), "data": data, "levels": dict(lv_store),
                    "contours": list(ids), "autocam": True, "autocam_sheet": sheet_no, "kind": kind}
        if drill:
            settings["drill"] = drill
        out["operations"].append({"name": name, "kind": kind, "tool": row, "contours": list(ids), "settings": settings, "data": data,
                                  "lv": dict(lv), "drill": drill})

    for no in sorted(drilled):
        _op("drill", by_no[no], drilled[no])
    for no in sorted(milled):
        _op("hole", by_no[no], milled[no])
    if outlines:
        row, bad = outline_tool(sheet, mills, margin, spacing)
        out["warnings"] += bad
        _op("outline", row, [o["id"] for o in outlines])
    return out


def program(name: str, sheet: dict, sheet_no: int, planned: dict, thickness: float, project: str = "", run_no=None,
            margin: float = 0.0, spacing: float = 0.0):
    """(program text, [the plan of every operation]) for the operations that plan() made."""
    return gcode_cam.sheet_program(name, sheet, sheet_no, planned["operations"], thickness, project, run_no, margin, spacing)
