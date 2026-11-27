"""NestoCAM, AutoCam: choose a sheet, press the button, and the operations (with all their steps filled) and the G-code are made."""
from __future__ import annotations

import pandas as pd
import streamlit as st

import autocam
from cutting_data_ui import get_defaults
from operations_ui import file_stem
from tools_ui import _row_to_tool


def _back():
    st.session_state.autocam_open = False


def _run(db, pid, result, project, meta, run_id):
    """Plan, make the operations (the ones AutoCam made for this sheet before are replaced) and the program."""
    ss = st.session_state
    sheet_no, thickness = int(ss.autocam_sheet), float(ss.autocam_thickness)
    sheet = result[sheet_no - 1]
    margin, spacing = float((meta or {}).get("margin", 0.0)), float((meta or {}).get("spacing", 0.0))
    ss.autocam_result = None
    try:
        tools = [_row_to_tool(r) for r in db.list_cam_tools()]
        planned = autocam.plan(sheet, sheet_no, tools, get_defaults(db), thickness, margin, spacing)
        if planned["problems"]:
            ss.autocam_result = {"errors": planned["problems"]}
            return
        db.delete_autocam_operations(run_id, sheet_no)
        for op in planned["operations"]:
            db.create_operation(run_id, op["name"], op["settings"])
        name = f"{file_stem(project)}-Run{run_id}-Sheet{sheet_no}-AutoCam.anc"
        text, plans = autocam.program(name, sheet, sheet_no, planned, thickness, project, run_id, margin, spacing)
    except Exception as e:  # noqa: BLE001
        ss.autocam_result = {"errors": [f"AutoCam could not finish: {e}"]}
        return
    warnings = list(planned["warnings"]) + planned["skipped"] + [w for p in plans for w in p.warnings]
    from camming_ui import solids, toolpath_png, tool_color_map
    paths = tuple((op["tool"]["number"], tuple(c.pts)) for op, p in zip(planned["operations"], plans) for c in p.contours)
    tools_used = {op["tool"]["number"]: op["tool"] for op in planned["operations"]}
    legend = [(n, f"**T{n}** {t['name']} Ø{t['diameter']:g}") for n, t in sorted(tools_used.items())]
    try:
        picture = toolpath_png(float(sheet["width"]), float(sheet["height"]), thickness, solids(sheet), paths) if paths else None
    except Exception:  # noqa: BLE001 - the picture is a help, the G-code does not depend on it
        picture = None
    ss.autocam_result = {"run_id": run_id, "sheet": sheet_no, "text": text, "name": name, "warnings": warnings, "picture": picture,
                         "legend": [(tool_color_map(tools_used)[n], t) for n, t in legend],
                         "rows": [{"Operation": op["name"], "What": {"drill": "drills the holes", "hole": "mills the holes", "outline": "mills the outlines"}[op["kind"]],
                                   "Tool": f"T{op['tool']['number']} {op['tool']['name']} Ø{op['tool']['diameter']:g}", "Contours": len(op["contours"])}
                                  for op in planned["operations"]]}


def autocam_page(db, pid, result, project: str = "", meta: dict | None = None, run_id=None):
    ss = st.session_state
    st.button("← Back to the operations", on_click=_back, key="autocam_back")
    st.subheader("AutoCam")
    st.caption("AutoCam finds the holes and the outlines of a sheet, chooses the tools, makes the operations with all their steps filled in "
               "and writes the G-code of the sheet: nobody has to choose anything. The rules: a round hole that is as wide as a drill of the "
               "tool library is drilled with that drill; every other hole is milled with the largest end mill that fits in it; the outlines "
               "are milled with the largest end mill that keeps clear of the neighbouring parts and the edge of the sheet. The drilled holes "
               "come first, then the milled holes, then the outlines. The cutting data come from CAM options (Cutting data).")
    if not run_id or not result:
        st.info("Nest first, or load a run: AutoCam works on a sheet of a stored run.")
        return
    n = len(result)
    if not 1 <= ss.get("autocam_sheet", 1) <= n:
        ss.autocam_sheet = 1
    if "autocam_thickness" not in ss:
        try:
            ss.autocam_thickness = float((db.latest_operation_settings(pid) or {}).get("thickness", 18.0))
        except Exception:  # noqa: BLE001
            ss.autocam_thickness = 18.0
    left, _ = st.columns([2, 3])
    with left.form("autocam_form", border=False):
        st.selectbox("Sheet", list(range(1, n + 1)), key="autocam_sheet", format_func=lambda i: f"Sheet {i}")
        st.number_input("Material thickness", min_value=0.1, step=0.5, key="autocam_thickness",
                        help="Height of the stock: the top of the material is at Z = thickness.")
        go = st.form_submit_button("▶ Run AutoCam", type="primary", key="autocam_run",
                                   help="Makes the operations of this sheet (the ones AutoCam made for it before are replaced) and its G-code.")
    if go:
        _run(db, pid, result, project, meta, run_id)
    res = ss.get("autocam_result")
    if not res:
        return
    if res.get("run_id") != run_id and not res.get("errors"):
        return
    for e in res.get("errors", []):
        st.error(e)
    if res.get("errors"):
        return
    st.success(f"AutoCam made {len(res['rows'])} operation(s) for sheet {res['sheet']}: they are in the list of operations, each with its "
               "five steps filled in.")
    st.dataframe(pd.DataFrame(res["rows"]), hide_index=True, width="content")
    for w in res["warnings"]:
        st.warning(w)
    if res.get("picture"):
        st.image(res["picture"], caption="The sheet in an isometric view (seen from the lower left corner) with the toolpaths on the top of "
                                         "the material: every path has the colour and the number of its tool.")
        st.markdown(" &nbsp;&nbsp; ".join(f'<span style="color:{col};font-size:1.2em">■</span> {text}' for col, text in res["legend"]),
                    unsafe_allow_html=True)
    shown = res["text"].replace("\r\n", "\n")
    with st.expander(f"G-code of sheet {res['sheet']} ({len(shown.splitlines()):,} lines)", expanded=False):
        st.code(shown, language=None, height=600)
    st.download_button(f"⬇ {res['name']}", lambda: res["text"].encode("ascii", "replace"), res["name"], "text/plain", key="dl_autocam",
                       on_click="ignore")
