"""NestoCAM step 5 - Generate G-code: the G-code of the sheet of the operation, from the saved values of the four steps."""
from __future__ import annotations

import matplotlib.pyplot as plt
import pandas as pd
import streamlit as st

import cam
import gcode_cam
import machine_profile as machine
from data_ui import get_data
from layout_edit import is_held
from levels_ui import get_levels, thickness_of
from camming_ui import saved_order
from operations_ui import file_stem
from tools_ui import chosen_tool


def step_gcode(db, pid, result, operation: str, project: str = "", meta: dict | None = None, run_id=None):
    st.caption(f"G-code for {machine.NAME} ({machine.CONTROLLER}), written like the SolidCAM post. It is made from the SAVED values of "
               "steps 1 to 4 of this operation (press Save in a step after changing it).")
    thk = thickness_of(db, pid)
    try:
        tool = chosen_tool(db, pid)
    except Exception as e:  # noqa: BLE001
        st.error(f"Could not read the tools: {e}")
        return
    data, lv = get_data(db, pid), get_levels(db, pid, thk)
    sheet_no = int((db.get_cam(pid) or {}).get("camming", {}).get("sheet", 1))
    meta = meta or {}
    margin, spacing = float(meta.get("margin", 0.0)), float(meta.get("spacing", 0.0))

    rows = [("Operation / sheet (step 1)", f"{operation} / sheet {sheet_no}"),
            ("Tool (step 2)", f"T{tool['number']}  {tool['name']}  Ø{tool['diameter']:g}" if tool else "none chosen"),
            ("Feed XY / Z (step 3)", f"{data['feed_xy']:g} / {data['feed_z']:g} mm/min"),
            ("Spin (step 3)", f"{data['rpm']:g} rpm {data['direction']}"),
            ("Clearance level / safety distance (step 4)", f"{lv['clearance']:g} / {lv['safety']:g}"),
            ("Upper level / profile depth (step 4)", f"{lv['upper']:g} / {lv['depth']:g}")]
    st.dataframe(pd.DataFrame(rows, columns=["Information", "Value"]), hide_index=True, width="content")
    with st.expander("What the steps do not say (assumed by this G-code)"):
        for a in gcode_cam.ASSUMPTIONS:
            st.markdown(f"- {a}")

    errs = gcode_cam.problems(tool, data, lv)
    for e in errs:
        st.error(e)
    if errs:
        return
    if not result:
        st.info("Nest first: the G-code is made from the sheets of the current run.")
        return
    for w in gcode_cam.warnings(tool, lv):
        st.warning(w)
    if not 1 <= sheet_no <= len(result):
        st.error(f"The sheet of this operation (sheet {sheet_no}) is not in this run. Choose a sheet in step 1.")
        return
    held = [p["part"] for r in result for p in r["placements"] if is_held(p)]
    if held:
        st.warning(f"{len(held)} part(s) are in the hold area and are not machined: {', '.join(held)}")
    sheet = result[sheet_no - 1]
    order_ids = saved_order(db, pid, sheet_no)
    plan, groups, _t, _job = gcode_cam.build(sheet, tool, data, lv, thk, margin, spacing, order_ids)
    for w in plan.warnings:
        st.warning(w)
    if not plan.contours:
        st.info("There is nothing to cut on this sheet.")
        return
    ext = machine.FILE_EXTENSION
    length = sum(cam._ring_length(c.pts) for c in plan.contours)
    minutes = cam.estimate_minutes(groups)
    m1, m2, m3 = st.columns(3)
    m1.metric("Contours", len(plan.contours), help="Holes and outlines of the parts on this sheet.")
    m2.metric("Contour length", f"{length:,.0f}")
    m3.metric("Estimated time", f"{minutes:.1f} min")
    from cam_ui import _preview
    fig = _preview(sheet, plan)
    st.pyplot(fig)
    plt.close(fig)

    name = f"{file_stem(operation)}.{ext}"
    gtext = gcode_cam.text(name, sheet, sheet_no, tool, data, lv, thk, project, run_id, margin, spacing, operation, order_ids)
    with st.expander(f"G-code of {name} ({len(gtext.splitlines()):,} lines)", expanded=True):
        st.code("\n".join(gtext.splitlines()[:200]) + ("\n..." if len(gtext.splitlines()) > 200 else ""), language=None)
    st.download_button(f"⬇ {name}", lambda: gtext.encode("ascii", "replace"), name, "text/plain", key="dl_nc", on_click="ignore")
