"""The "Machining" tab: tool library, machining settings, toolpath preview and G-code download."""
from __future__ import annotations

import io
import re
import zipfile

import matplotlib
import pandas as pd
import streamlit as st

import cam
import machine_profile as machine
import post_fanuc
from layout_edit import is_held, placement_geom

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

SETTING_KEYS = ["thickness", "overcut", "clearance", "approach", "ramp", "climb", "tabs", "tab_width", "tab_height",
                "inner_tool", "outer_tool", "smoothing", "extension", "programmer"]
DEFAULT_TOOL = {"T": 1, "Name": "END MILL 6 MM 2 FLUTE", "Diameter": 6.0, "Flutes": 2, "RPM": 12000.0, "Feed": 4000.0,
                "Plunge": 1500.0, "Step-down": 6.0, "Coolant": "none"}


def _tools_from_rows(rows) -> tuple[dict, list[str]]:
    tools, problems = {}, []
    for r in rows:
        try:
            t = cam.Tool(int(r["T"]), str(r.get("Name") or ""), float(r["Diameter"]), int(r.get("Flutes") or 2), float(r["RPM"]),
                         float(r["Feed"]), float(r["Plunge"]), float(r["Step-down"]), str(r.get("Coolant") or "none"))
        except (TypeError, ValueError, KeyError):
            problems.append("Every tool needs a number, a diameter, a spindle speed, a feed, a plunge feed and a step-down.")
            continue
        problems += t.problems()
        if t.number in tools:
            problems.append(f"Tool number T{t.number} is used twice.")
        tools[t.number] = t
    return tools, problems


def _load_settings(db, pid):
    """Machining settings of the project into the widgets (once per project)."""
    if st.session_state.get("cam_loaded_pid") == pid:
        return
    saved = {}
    try:
        saved = db.get_cam(pid) or {}
    except Exception:  # noqa: BLE001 - machining storage is optional
        saved = {}
    defaults = cam.CamJob().__dict__
    for k in SETTING_KEYS:
        st.session_state[f"cam_{k}"] = saved.get(k, defaults[k])
    st.session_state.cam_loaded_pid = pid


def _preview(sheet: dict, plan: cam.Plan):
    fig, ax = plt.subplots(figsize=(8, 8 * sheet["height"] / sheet["width"] if sheet["width"] else 5), dpi=110)
    ax.add_patch(plt.Rectangle((0, 0), sheet["width"], sheet["height"], fill=False, lw=1.5, color="black"))
    for p in sheet["placements"]:
        if is_held(p):
            continue
        g = placement_geom(p)
        for poly in ([g] if g.geom_type == "Polygon" else g.geoms):
            xs, ys = poly.exterior.xy
            ax.fill(xs, ys, color="#e2e8f0", ec="#94a3b8", lw=0.6)
            for h in poly.interiors:
                hx, hy = h.xy
                ax.fill(hx, hy, color="white", ec="#94a3b8", lw=0.6)
    for k, c in enumerate(plan.contours):
        xs, ys = zip(*c.pts)
        ax.plot(xs, ys, color="#2563eb" if c.kind == "inner" else "#dc2626", lw=0.9)
        ax.plot([xs[0]], [ys[0]], "o", color="#16a34a", ms=3)
    ax.set_aspect("equal")
    xs_all = [0.0, sheet["width"]] + [x for c in plan.contours for x, _ in c.pts]       # the sheet and every toolpath are in the picture
    ys_all = [0.0, sheet["height"]] + [y for c in plan.contours for _, y in c.pts]
    pad = 0.02 * max(sheet["width"], sheet["height"])
    ax.set_xlim(min(xs_all) - pad, max(xs_all) + pad)
    ax.set_ylim(min(ys_all) - pad, max(ys_all) + pad)
    return fig


def machining_tab(db, pid, project: str, result, meta: dict, run_id, file_base: str):
    st.caption(f"G-code for {machine.NAME} ({machine.CONTROLLER}), written like the SolidCAM post: work offset G{machine.WORK_OFFSET} "
               "with its zero at the lower left corner of the sheet and Z0 at the top of the material; metric, absolute.")
    # ---- tool library ------------------------------------------------------------------------------------------------
    try:
        stored = list(db.list_tools())
    except Exception as e:  # noqa: BLE001
        st.error(f"Could not read the tool library: {e}")
        stored = []
    rows = [{"T": t["number"], "Name": t["name"], "Diameter": t["diameter"], "Flutes": t["flutes"], "RPM": t["rpm"],
             "Feed": t["feed"], "Plunge": t["plunge"], "Step-down": t["step_down"], "Coolant": t["coolant"]} for t in stored]
    with st.expander("Tools", expanded=not stored):
        st.caption("One library for all projects. T = tool position of the machine (1 to "
                   f"{machine.MAX_TOOL_NUMBER}; the length offset H is the same number). Feed and plunge in mm/min, "
                   f"spindle up to {machine.MAX_RPM:g} rpm.")
        df = pd.DataFrame(rows or [DEFAULT_TOOL])
        edited = st.data_editor(df, num_rows="dynamic", width="stretch", hide_index=True, key=f"cam_tools_{len(rows)}",
                                column_config={"T": st.column_config.NumberColumn("T", min_value=1, max_value=machine.MAX_TOOL_NUMBER, step=1, width=50),
                                               "Name": "Name", "Diameter": st.column_config.NumberColumn("Ø mm", min_value=0.1),
                                               "Flutes": st.column_config.NumberColumn("Flutes", min_value=1, step=1, width=60),
                                               "RPM": st.column_config.NumberColumn("RPM", min_value=1, max_value=machine.MAX_RPM),
                                               "Feed": st.column_config.NumberColumn("Feed", min_value=1),
                                               "Plunge": st.column_config.NumberColumn("Plunge", min_value=1),
                                               "Step-down": st.column_config.NumberColumn("Step-down", min_value=0.1),
                                               "Coolant": st.column_config.SelectboxColumn("Coolant", options=["none", "flood", "air"], required=True)})
        if st.button("Save tools", key="cam_save_tools"):
            new, problems = _tools_from_rows(edited.dropna(subset=["T"]).to_dict("records"))
            if problems:
                for p in dict.fromkeys(problems):
                    st.error(p)
            else:
                try:
                    db.save_tools([{"number": t.number, "name": t.name, "diameter": t.diameter, "flutes": t.flutes, "rpm": t.rpm,
                                    "feed": t.feed, "plunge": t.plunge, "step_down": t.step_down, "coolant": t.coolant}
                                   for t in new.values()])
                    st.toast(f"{len(new)} tool(s) saved.", icon=":material/check_circle:")
                    st.rerun()
                except Exception as e:  # noqa: BLE001
                    st.error(f"Could not save the tools: {e}")
    tools, _ = _tools_from_rows(rows)
    if not tools:
        st.info("Define at least one tool (open **Tools** above and press **Save tools**) to make toolpaths.")
        return
    # ---- machining settings --------------------------------------------------------------------------------------------
    _load_settings(db, pid)
    labels = {n: f"T{n}  {t.name or 'tool'}  Ø{t.diameter:g}" for n, t in tools.items()}
    st.subheader("Settings")
    c1, c2, c3 = st.columns(3)
    c1.number_input("Material thickness", min_value=0.0, step=0.5, key="cam_thickness", help="Z0 is the top of the material.")
    c2.number_input("Overcut below the material", min_value=0.0, step=0.1, key="cam_overcut",
                    help="The tool goes this far below the material so that the parts are cut through.")
    c3.number_input("Smoothing (G05.1 R)", min_value=0.0, max_value=10.0, step=1.0, key="cam_smoothing",
                    help="AICC high speed mode like the post (default 7). 0 = off.")
    c1, c2, c3 = st.columns(3)
    c1.number_input("Rapid height above the material", min_value=1.0, step=1.0, key="cam_clearance")
    c2.number_input("Feed-in height above the material", min_value=0.1, step=0.5, key="cam_approach",
                    help="The tool goes down at rapid speed to here, then at the plunge feed.")
    c3.selectbox("Going down", [True, False], key="cam_ramp", format_func=lambda v: "Spiral along the contour" if v else "Straight plunge")
    c1, c2, c3 = st.columns(3)
    c1.selectbox("Milling", [True, False], key="cam_climb", format_func=lambda v: "Climb" if v else "Conventional")
    inner_opts = list(tools)
    for key in ("cam_inner_tool", "cam_outer_tool"):
        if st.session_state.get(key) not in tools:
            st.session_state[key] = inner_opts[0]
    c2.selectbox("Tool for holes", inner_opts, key="cam_inner_tool", format_func=lambda n: labels[n])
    c3.selectbox("Tool for outlines", inner_opts, key="cam_outer_tool", format_func=lambda n: labels[n])
    c1, c2, c3 = st.columns(3)
    c1.number_input("Tabs per outline", min_value=0, max_value=12, step=1, key="cam_tabs",
                    help="Small bridges that hold a part to the sheet on the last pass (0 = none, e.g. with a vacuum table).")
    c2.number_input("Tab width", min_value=1.0, step=1.0, key="cam_tab_width")
    c3.number_input("Tab height (material left)", min_value=0.1, step=0.5, key="cam_tab_height")
    c1, c2 = st.columns(2)
    c1.text_input("File extension", key="cam_extension", help=f"The post writes .{machine.FILE_EXTENSION}")
    c2.text_input("Programmed by (written in the file header)", key="cam_programmer")
    if st.button("Save machining settings", key="cam_save"):
        try:
            db.save_cam(pid, {k: st.session_state[f"cam_{k}"] for k in SETTING_KEYS})
            st.toast("Machining settings saved with the project.", icon=":material/check_circle:")
        except Exception as e:  # noqa: BLE001
            st.error(f"Could not save the settings: {e}")
    job = cam.CamJob(**{k: st.session_state[f"cam_{k}"] for k in SETTING_KEYS})
    job.extension = re.sub(r"[^A-Za-z0-9]", "", str(job.extension)) or machine.FILE_EXTENSION
    # ---- preview and download --------------------------------------------------------------------------------------------
    if not result:
        st.info("Nest first: the toolpaths are made from the sheets of the current run.")
        return
    st.subheader("Toolpaths")
    n_sheets = len(result)
    sheet_no = st.selectbox("Sheet", list(range(1, n_sheets + 1)), key="cam_sheet", format_func=lambda i: f"Sheet {i}")
    margin, spacing = meta["margin"], meta["spacing"]
    ok = job.inner_tool in tools and job.outer_tool in tools
    held = [p["part"] for r in result for p in r["placements"] if is_held(p)]
    if held:
        st.warning(f"{len(held)} part(s) are in the hold area and are not machined: {', '.join(held)}")
    if st.toggle("Show the toolpaths, warnings and G-code of this sheet", key="cam_preview",
                 help="Switch off while you edit other things: the toolpaths are made again at every change."):
        plan = cam.plan_sheet(result[sheet_no - 1], job, tools, margin, spacing)
        for w in plan.warnings:
            st.warning(w)
        if plan.contours:
            grp = cam.groups(plan, job, tools)
            minutes = cam.estimate_minutes([(t, m) for t, m in grp])
            length = sum(cam._ring_length(c.pts) for c in plan.contours)
            m1, m2, m3 = st.columns(3)
            m1.metric("Contours", len(plan.contours), help="Holes and outlines of the parts on this sheet.")
            m2.metric("Contour length", f"{length:,.0f}", help="One pass around every contour (the tool centre path).")
            m3.metric("Estimated time", f"{minutes:.1f} min")
            fig = _preview(result[sheet_no - 1], plan)
            st.pyplot(fig)
            plt.close(fig)
            text = post_fanuc.program(f"{file_base}_sheet_{sheet_no:02d}.{job.extension}", grp, job, sheet_no,
                                      (result[sheet_no - 1]["width"], result[sheet_no - 1]["height"]), project, run_id,
                                      {"length": length, "minutes": minutes})
            with st.expander(f"G-code of sheet {sheet_no} ({len(text.splitlines()):,} lines)"):
                st.code("\n".join(text.splitlines()[:150]) + ("\n..." if len(text.splitlines()) > 150 else ""), language=None)

    snapshot_tools, snapshot_job, result_ref = dict(tools), job, result

    def _one(i):
        p = cam.plan_sheet(result_ref[i], snapshot_job, snapshot_tools, margin, spacing)
        g = cam.groups(p, snapshot_job, snapshot_tools)
        ln = sum(cam._ring_length(c.pts) for c in p.contours)
        return post_fanuc.program(f"{file_base}_sheet_{i + 1:02d}.{snapshot_job.extension}", g, snapshot_job, i + 1,
                                  (result_ref[i]["width"], result_ref[i]["height"]), project, run_id,
                                  {"length": ln, "minutes": cam.estimate_minutes(g)})

    def _zip():
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for i in range(len(result_ref)):
                z.writestr(f"{file_base}_sheet_{i + 1:02d}.{snapshot_job.extension}", _one(i))
        return buf.getvalue()

    d1, d2 = st.columns(2)
    d1.download_button(f"⬇ G-code – sheet {sheet_no}", lambda: _one(sheet_no - 1).encode("ascii", "replace"),
                       f"{file_base}_sheet_{sheet_no:02d}.{job.extension}", "text/plain", width="stretch",
                       key="dl_gcode", on_click="ignore", disabled=not ok)
    d2.download_button(f"⬇ G-code – all {n_sheets} sheets (ZIP)", _zip, f"{file_base}_gcode.zip", "application/zip",
                       width="stretch", key="dl_gcode_zip", on_click="ignore", disabled=not ok)
