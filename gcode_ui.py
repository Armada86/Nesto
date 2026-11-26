"""NestoCAM step 5 - Generate G-code: the G-code of a sheet from the saved values of the four steps."""
from __future__ import annotations

import io
import zipfile

import matplotlib.pyplot as plt
import pandas as pd
import streamlit as st

import cam
import gcode_cam
import machine_profile as machine
from data_ui import get_data
from layout_edit import is_held
from levels_ui import get_levels, thickness_of
from tools_ui import chosen_tool


def step_gcode(db, pid, result, project: str = "", meta: dict | None = None, run_id=None, file_base: str = "nest"):
    st.caption(f"G-code for {machine.NAME} ({machine.CONTROLLER}), written like the SolidCAM post. It is made from the SAVED values of "
               "steps 1 to 4 (press Save in a step after changing it).")
    thk = thickness_of(db, pid)
    try:
        tool = chosen_tool(db, pid)
    except Exception as e:  # noqa: BLE001
        st.error(f"Could not read the tools: {e}")
        return
    data, lv = get_data(db, pid), get_levels(db, pid, thk)
    meta = meta or {}
    margin, spacing = float(meta.get("margin", 0.0)), float(meta.get("spacing", 0.0))

    rows = [("Tool (step 2)", f"T{tool['number']}  {tool['name']}  Ø{tool['diameter']:g}" if tool else "none chosen"),
            ("Feed XY / Z (step 3)", f"{data['feed_xy']:g} / {data['feed_z']:g} mm/min"),
            ("Feed lead in / out (step 3)", f"{data['lead_in']:g} % / {data['lead_out']:g} %"),
            ("Spin (step 3)", f"{data['rpm']:g} rpm {data['direction']}"),
            ("Clearance level / safety distance (step 4)", f"{lv['clearance']:g} / {lv['safety']:g}"),
            ("Upper level / profile depth (step 4)", f"{lv['upper']:g} / {lv['depth']:g}")]
    st.dataframe(pd.DataFrame(rows, columns=["Information", "Value"]), hide_index=True, width="stretch")
    with st.expander("What the four steps do not say (assumed by this first G-code)"):
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
    sheet_no = st.selectbox("Sheet", list(range(1, len(result) + 1)), key="gcode_sheet", format_func=lambda i: f"Sheet {i}")
    held = [p["part"] for r in result for p in r["placements"] if is_held(p)]
    if held:
        st.warning(f"{len(held)} part(s) are in the hold area and are not machined: {', '.join(held)}")
    sheet = result[sheet_no - 1]
    plan, groups, _t, _job = gcode_cam.build(sheet, tool, data, lv, thk, margin, spacing)
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

    snap = (list(result), dict(tool), dict(data), dict(lv), thk, project, run_id, margin, spacing)

    def _one(i):
        res, t, d, l, th, proj, rid, mg, sp = snap
        return gcode_cam.text(f"{file_base}_sheet_{i + 1:02d}.{ext}", res[i], i + 1, t, d, l, th, proj, rid, mg, sp)

    gtext = _one(sheet_no - 1)
    with st.expander(f"G-code of sheet {sheet_no} ({len(gtext.splitlines()):,} lines)", expanded=True):
        st.code("\n".join(gtext.splitlines()[:200]) + ("\n..." if len(gtext.splitlines()) > 200 else ""), language=None)

    def _zip():
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for i in range(len(snap[0])):
                z.writestr(f"{file_base}_sheet_{i + 1:02d}.{ext}", _one(i))
        return buf.getvalue()

    d1, d2 = st.columns(2)
    d1.download_button(f"⬇ G-code – sheet {sheet_no}", lambda: _one(sheet_no - 1).encode("ascii", "replace"),
                       f"{file_base}_sheet_{sheet_no:02d}.{ext}", "text/plain", width="stretch", key="dl_nc", on_click="ignore")
    d2.download_button(f"⬇ G-code – all {len(result)} sheets (ZIP)", _zip, f"{file_base}_gcode.zip", "application/zip", width="stretch",
                       key="dl_nc_zip", on_click="ignore")
