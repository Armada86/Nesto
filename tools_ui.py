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
            "rough": False, "flutes": 2}


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
    t = {k: r[k] for k in ("number", "tool_type", "name", "rough", "flutes")}
    for k, _l, _h, _d in DIMS:
        t[k] = float(r[k])
    t["helical_angle"] = float(r["helical_angle"])
    return t


def _load_form(t: dict):
    for k, v in t.items():
        st.session_state[PREFIX + k] = v


def _form_tool() -> dict:
    t = {k: st.session_state[PREFIX + k] for k in ("number", "tool_type", "name", "rough", "flutes", "helical_angle")}
    for k, _l, _h, _d in DIMS:
        t[k] = float(st.session_state[PREFIX + k])
    return t


def step_tools(db):
    st.caption("The tool library, shared by all projects. Choose a tool to change or delete it, or choose **New tool**. "
               "The picture only explains what each dimension means; it does not follow the numbers.")
    try:
        tools = [_row_to_tool(r) for r in db.list_cam_tools()]
    except Exception as e:  # noqa: BLE001
        st.error(f"Could not read the tools: {e}")
        return
    by_no = {t["number"]: t for t in tools}

    if tools:
        df = pd.DataFrame([{"No.": t["number"], "Name": t["name"], "Type": t["tool_type"], "D": t["diameter"], "Flutes": t["flutes"],
                            "CL": t["cutting_length"], "TL": t["total_length"], "OHL": t["outside_holder"],
                            "Helix": t["helical_angle"], "Rough": "yes" if t["rough"] else ""} for t in tools])
        st.dataframe(df, hide_index=True, width="stretch")
    else:
        st.info("No tools yet. Fill the form below and press **Save tool**.")

    def _sel_changed():
        n = st.session_state.get("tools_sel")
        _load_form(by_no[n] if n in by_no else blank(max(by_no, default=0) + 1))
        st.session_state.tools_old = n if n in by_no else None

    options = [0, *by_no]                                       # 0 = a new tool
    if "tools_sel" not in st.session_state or st.session_state.tools_sel not in options:
        st.session_state.tools_sel = 0
    if "tools_old" not in st.session_state or PREFIX + "diameter" not in st.session_state:
        _sel_changed()
    st.selectbox("Tool", options, key="tools_sel", on_change=_sel_changed,
                 format_func=lambda n: "➕ New tool" if not n else f"T{n}  {by_no[n]['name'] or by_no[n]['tool_type'].title()}  Ø{by_no[n]['diameter']:g}")

    old = st.session_state.get("tools_old")
    msg = st.session_state.pop("tools_msg", None)
    if msg:
        st.toast(msg, icon=":material/check_circle:")

    def _save():
        t = _form_tool()
        problems = validate(t, {n for n in by_no if n != old})
        if problems:
            st.session_state.tools_problems = problems
            return
        try:
            db.save_cam_tool({**t, "number": int(t["number"]), "flutes": int(t["flutes"])}, old)
        except Exception as e:  # noqa: BLE001
            st.session_state.tools_problems = [f"Could not save: {e}"]
            return
        st.session_state.tools_problems = []
        st.session_state.tools_old = int(t["number"])
        st.session_state.tools_sel = int(t["number"])
        st.session_state.tools_msg = f"Tool T{int(t['number'])} saved."

    def _delete():
        try:
            db.delete_cam_tool(old)
        except Exception as e:  # noqa: BLE001
            st.session_state.tools_problems = [f"Could not delete: {e}"]
            return
        st.session_state.tools_msg = f"Tool T{old} deleted."
        st.session_state.tools_sel = 0
        _sel_changed()

    left, right = st.columns([3, 2])
    with left:
        a, b = st.columns(2)
        a.number_input("Catalog number (T)", min_value=1, step=1, key=PREFIX + "number", help="The tool number used in the G-code (T and H).")
        b.text_input("Name (optional)", key=PREFIX + "name")
        a.selectbox("Tool type", TOOL_TYPES, key=PREFIX + "tool_type")
        b.number_input("Number of flutes", min_value=1, step=1, key=PREFIX + "flutes")
        st.markdown("**Tool parameters** (mm and degrees)")
        c1, c2 = st.columns(2)
        for i, (k, lab, hlp, _d) in enumerate(DIMS):
            (c1 if i % 2 == 0 else c2).number_input(lab + (" [°]" if k in ANGLES else " [mm]"), min_value=0.0, step=0.5, format="%.3f",
                                                    key=PREFIX + k, help=hlp)
        c1.number_input("Helical angle [°]", min_value=0.0, max_value=89.0, step=1.0, format="%.1f", key=PREFIX + "helical_angle",
                        help="30 is the standard helix.")
        c2.checkbox("Rough", key=PREFIX + "rough", help="A roughing tool (serrated flutes).")
        for p in st.session_state.get("tools_problems", []):
            st.error(p)
        s, d, _ = st.columns([1, 1, 2])
        s.button("Save tool", type="primary", on_click=_save, key="tools_save")
        if old is not None:
            with d.popover("Delete tool"):
                st.write(f"Delete tool **T{old}**?")
                st.button("Yes, delete", on_click=_delete, key="tools_delete")
    with right:
        st.image(GUIDE_SVG)
        st.caption("D diameter · SD shoulder diameter · AD arbor diameter · SA shoulder angle · CL cutting length · "
                   "SL shoulder length · TL total length · CH chamfer. OHL (outside holder) is the length that sticks out of the holder.")
