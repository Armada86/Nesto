"""NestoCAM step 2 - Tools: the tool library (add, change, delete) with the dimensions a tool needs for G-code."""
from __future__ import annotations

import pandas as pd
import streamlit as st

TOOL_TYPES = ["END MILL"]
# (key, label, help, default, minimum, maximum)
DIMS = [
    ("diameter", "Diameter (D)", "Cutting diameter of the tool.", 12.0),
    ("shoulder_diameter", "Shoulder diameter (SD)", "Diameter of the shank above the flutes.", 12.0),
    ("arbor_diameter", "Arbor diameter (AD)", "Diameter of the part held in the holder.", 12.0),
    ("shoulder_angle", "Shoulder angle (SA)", "Angle of the step between the cutting part and the shank (0 = none).", 0.0),
    ("cutting_length", "Cutting length (CL)", "Length of the flutes, measured from the tip.", 30.0),
    ("shoulder_length", "Shoulder length (SL)", "Length from the tip to the end of the shoulder.", 40.0),
    ("total_length", "Total length (TL)", "Length of the tool from the tip to the top of the arbor.", 40.0),
    ("chamfer", "Chamfer (CH)", "Size of the 45 degree chamfer at the tip (0 = none).", 0.0),
    ("outside_holder", "Outside holder (OHL)", "Length of the tool sticking out of the holder, from the tip.", 100.0),
]
ANGLES = {"shoulder_angle", "helical_angle"}
PREFIX = "tl_"


def blank(number: int = 1) -> dict:
    return {"number": number, "tool_type": TOOL_TYPES[0], "name": "", **{k: d for k, _l, _h, d in DIMS}, "helical_angle": 30.0,
            "flutes": 2, "holder": ""}


def validate(t: dict, others: set) -> list[str]:
    """What is wrong with this tool (empty list = fine)."""
    p = []
    if not t["number"] or int(t["number"]) < 1:
        p.append("The catalog number must be 1 or more.")
    elif int(t["number"]) in others:
        p.append(f"Catalog number {int(t['number'])} is already used by another tool.")
    for k, lab, _h, _d in DIMS:
        if k not in ("shoulder_angle", "chamfer") and not t[k] > 0:
            p.append(f"{lab} must be more than 0.")
    if t["cutting_length"] > t["shoulder_length"] > 0:
        p.append("Cutting length (CL) cannot be longer than the shoulder length (SL).")
    if t["shoulder_length"] > t["total_length"] > 0:
        p.append("Shoulder length (SL) cannot be longer than the total length (TL).")
    if t["total_length"] > t["outside_holder"] > 0:
        p.append("Outside holder (OHL) cannot be shorter than the total length (TL).")
    if t["chamfer"] < 0 or t["chamfer"] * 2 > t["diameter"]:
        p.append("The chamfer (CH) must be between 0 and half the diameter.")
    if not 0 <= t["helical_angle"] < 90:
        p.append("The helical angle must be between 0 and 90 degrees.")
    if int(t["flutes"]) < 1:
        p.append("The number of flutes must be 1 or more.")
    return p


GUIDE_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 330 430" font-family="sans-serif" font-size="13">
<defs><marker id="a" viewBox="0 0 8 8" refX="4" refY="4" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
<path d="M0,0 L8,4 L0,8 z" fill="#2563eb"/></marker></defs>
<rect x="125" y="20" width="70" height="75" fill="#d1d5db" stroke="#6b7280"/>
<rect x="115" y="95" width="90" height="25" fill="#cbd5e1" stroke="#6b7280"/>
<rect x="115" y="120" width="90" height="265" fill="#e5e7eb" stroke="#6b7280"/>
<path d="M115 175 q40 -22 90 -30 M115 235 q40 -22 90 -30 M115 295 q40 -22 90 -30 M115 355 q40 -22 90 -30" fill="none" stroke="#9ca3af" stroke-width="3"/>
<path d="M115 385 l-12 -10 M205 385 l12 -10" stroke="#6b7280"/>
<g stroke="#2563eb" stroke-width="1.2" fill="none" marker-start="url(#a)" marker-end="url(#a)">
<line x1="125" y1="40" x2="195" y2="40"/><line x1="115" y1="108" x2="205" y2="108"/>
<line x1="95" y1="20" x2="95" y2="385"/><line x1="255" y1="95" x2="255" y2="385"/><line x1="295" y1="190" x2="295" y2="385"/>
<line x1="115" y1="405" x2="205" y2="405"/></g>
<g fill="#2563eb"><text x="135" y="34">AD</text><text x="212" y="112">SD</text><text x="72" y="200">TL</text>
<text x="262" y="260">SL</text><text x="302" y="290">CL</text><text x="153" y="422">D</text><text x="222" y="398">CH x 45</text>
<text x="224" y="82">SA</text></g>
<path d="M205 95 l14 -12" stroke="#2563eb" fill="none"/>
</svg>"""


def _row_to_tool(r) -> dict:
    t = {k: r[k] for k in ("number", "tool_type", "name", "flutes")}
    t["holder"] = r.get("holder") or ""
    for k, _l, _h, _d in DIMS:
        t[k] = float(r[k])
    t["helical_angle"] = float(r["helical_angle"])
    return t


def _load_form(t: dict):
    for k, v in t.items():
        st.session_state[PREFIX + k] = v


def _form_tool() -> dict:
    t = {k: st.session_state[PREFIX + k] for k in ("number", "tool_type", "name", "flutes", "helical_angle", "holder")}
    for k, _l, _h, _d in DIMS:
        t[k] = float(st.session_state[PREFIX + k])
    return t


def _cam_settings(db, pid) -> dict:
    try:
        return (db.get_cam(pid) or {}).get("camming", {})
    except Exception:  # noqa: BLE001 - the saved choice is optional
        return {}


def chosen_tool(db, pid) -> dict | None:
    """The tool chosen for the CAM of this operation (the next steps use it), or None."""
    no = _cam_settings(db, pid).get("tool")
    if no is None:
        return None
    return next((_row_to_tool(r) for r in db.list_cam_tools() if r["number"] == no), None)


def step_tools(db, pid):
    st.caption("The tool library, shared by all projects. Click a tool in the list to see and change its dimensions, delete it, or "
               "choose it for the CAM of this operation. The picture only explains what each dimension means; it does not follow the numbers.")
    try:
        tools = [_row_to_tool(r) for r in db.list_cam_tools()]
    except Exception as e:  # noqa: BLE001
        st.error(f"Could not read the tools: {e}")
        return
    by_no = {t["number"]: t for t in tools}
    used = _cam_settings(db, pid).get("tool")
    if used not in by_no:
        used = None
    ss = st.session_state
    ss.setdefault("tools_sel", None)           # None: nothing open, 0: a new tool, n: tool n
    ss.setdefault("tools_ver", 0)              # a new number gives the list a fresh, empty selection
    ss.setdefault("tools_prev_rows", ())
    if ss.tools_sel and ss.tools_sel not in by_no:
        ss.tools_sel = None

    def _open(n):
        ss.tools_sel = n
        ss.tools_confirm = False
        ss.tools_edit = n not in by_no                          # an existing tool is read-only until Edit is pressed
        ss.tools_problems = []
        _load_form(by_no[n] if n in by_no else blank(max(by_no, default=0) + 1))

    def _reset_list():
        ss.tools_ver += 1
        ss.tools_prev_rows = ()

    def _new():
        _reset_list()
        _open(0)

    def _save():
        t = _form_tool()
        old = ss.tools_sel or None
        problems = validate(t, {n for n in by_no if n != old})
        if problems:
            ss.tools_problems = problems
            return
        try:
            db.save_cam_tool({**t, "number": int(t["number"]), "flutes": int(t["flutes"])}, old)
        except Exception as e:  # noqa: BLE001
            ss.tools_problems = [f"Could not save: {e}"]
            return
        if old is not None and used == old and int(t["number"]) != old:      # the chosen tool was renumbered: keep it chosen
            _use(int(t["number"]))
        ss.tools_problems = []
        ss.tools_edit = False
        ss.tools_sel = int(t["number"])
        ss.tools_msg = f"Tool T{int(t['number'])} saved."
        _reset_list()

    def _delete():
        n = ss.tools_sel
        try:
            db.delete_cam_tool(n)
            if used == n:
                _use(None)
        except Exception as e:  # noqa: BLE001
            ss.tools_problems = [f"Could not delete: {e}"]
            return
        ss.tools_confirm = False
        ss.tools_msg = f"Tool T{n} deleted."
        ss.tools_sel = None
        _reset_list()

    def _edit():
        ss.tools_edit = True

    def _cancel():                                            # drop the changes: back to the saved values
        ss.tools_problems = []
        if ss.tools_sel in by_no:
            _load_form(by_no[ss.tools_sel])
            ss.tools_edit = False
        else:
            ss.tools_sel = None

    def _ask_delete():
        ss.tools_confirm = True

    def _keep():
        ss.tools_confirm = False

    def _use(n):
        try:
            cam = db.get_cam(pid) or {}
            cam["camming"] = {**cam.get("camming", {}), "tool": n}
            db.save_cam(pid, cam)
        except Exception as e:  # noqa: BLE001
            ss.tools_problems = [f"Could not save the choice: {e}"]
            return
        if n is not None:
            ss.tools_msg = f"Tool T{n} is the tool for the CAM of this operation."

    msg = ss.pop("tools_msg", None)
    if msg:
        st.toast(msg, icon=":material/check_circle:")

    top = st.columns([1, 1.4, 3])
    top[0].button("➕ New tool", on_click=_new, key="tools_new")
    sel = ss.tools_sel or None
    if sel is not None and sel == used:
        top[1].button("Do not use for CAM", on_click=_use, args=(None,), key="tools_unuse")
    else:
        top[1].button("Use for CAM", on_click=_use, args=(sel,), key="tools_use", disabled=sel is None, type="primary",
                      help="Tick a tool in the list, then press this to choose it for the next CAM steps of this operation.")
    if used is not None:
        u = by_no[used]
        top[2].markdown(f"Tool for the CAM of this operation: **T{used}**  {u['name']}  Ø{u['diameter']:g}")
    else:
        top[2].markdown("Tool for the CAM of this operation: **none chosen**")

    open_ = ss.tools_sel is not None
    list_col, detail_col = st.columns([2, 5])             # the list keeps the same narrow width, selected or not
    with list_col:
        if tools:
            df = pd.DataFrame([{"No.": t["number"], "Name": t["name"], "Type": t["tool_type"], "Diameter": t["diameter"], "Flutes": t["flutes"]} for t in tools])
            ev = st.dataframe(df, hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row",
                              key=f"tools_table_{ss.tools_ver}")
            rows = tuple(ev.selection.rows)
            if rows != ss.tools_prev_rows:                     # the person clicked another row, or the same row again to close it
                ss.tools_prev_rows = rows
                if rows:
                    _open(tools[rows[0]]["number"])
                    st.rerun()
                elif ss.tools_sel:
                    ss.tools_sel = None
                    st.rerun()
        else:
            st.info("No tools yet. Press **New tool**, fill the form and press **Save tool**.")

    if not open_:
        return
    old = ss.tools_sel or None
    with detail_col:
        editing = bool(ss.get("tools_edit"))
        lock = not editing
        left, right = st.columns([3, 2])
        with left:
            with st.form("tools_form", border=False):            # nothing is sent until a button is pressed: typing never reruns the page
                st.subheader("New tool" if old is None else f"Tool T{old}")
                bar = st.columns([1, 1, 1, 2])
                if editing:
                    bar[0].form_submit_button("Cancel", on_click=_cancel, key="tools_cancel")
                else:
                    bar[0].form_submit_button("✏️ Edit", on_click=_edit, key="tools_edit_btn")
                bar[1].form_submit_button("Save tool", type="primary", on_click=_save, key="tools_save", disabled=lock)
                if old is not None:
                    bar[2].form_submit_button("Delete", on_click=_ask_delete, key="tools_ask_delete")
                if old is not None and ss.get("tools_confirm"):
                    st.warning(f"Delete tool T{old} completely?")
                    y, n_, _ = st.columns([1, 1, 2])
                    y.form_submit_button("Yes, delete", on_click=_delete, key="tools_delete")
                    n_.form_submit_button("Keep it", on_click=_keep, key="tools_keep")
                for p in ss.get("tools_problems", []):
                    st.error(p)
                a, b = st.columns(2)
                a.number_input("Catalog number (T)", min_value=1, step=1, key=PREFIX + "number", help="The tool number used in the G-code (T and H).", disabled=lock)
                b.text_input("Name (optional)", key=PREFIX + "name", disabled=lock)
                a.selectbox("Tool type", TOOL_TYPES, key=PREFIX + "tool_type", disabled=lock)
                b.number_input("Number of flutes", min_value=1, step=1, key=PREFIX + "flutes", disabled=lock)
                a.text_input("Holder (optional)", key=PREFIX + "holder", disabled=lock,
                             help="The name of the tool holder, written in the header of the G-code (for example ADAPTOR).")
                st.markdown("**Tool parameters** (mm and degrees)")
                c1, c2 = st.columns(2)
                for i, (k, lab, hlp, _d) in enumerate(DIMS):
                    (c1 if i % 2 == 0 else c2).number_input(lab + (" [°]" if k in ANGLES else " [mm]"), min_value=0.0, step=0.5, format="%.3f",
                                                            key=PREFIX + k, help=hlp, disabled=lock)
                c1.number_input("Helical angle [°]", min_value=0.0, max_value=89.0, step=1.0, format="%.1f", key=PREFIX + "helical_angle",
                                help="30 is the standard helix.", disabled=lock)
        with right:
            st.image(GUIDE_SVG)
            st.caption("D diameter · SD shoulder diameter · AD arbor diameter · SA shoulder angle · CL cutting length · "
                       "SL shoulder length · TL total length · CH chamfer. OHL (outside holder) is the length that sticks out of the holder.")
