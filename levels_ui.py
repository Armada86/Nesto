"""NestoCAM step 4 - Levels: the Z levels of the toolpaths. Z = 0 is the bottom of the parts, the top of the material is Z = thickness."""
from __future__ import annotations

import streamlit as st

DEFAULTS = {"clearance": 45.0, "safety": 2.0}
PREFIX = "lv_"


def _saved(db, pid) -> dict:
    try:
        return (db.get_cam(pid) or {}).get("camming", {})
    except Exception:  # noqa: BLE001 - the saved values are optional
        return {}


def thickness_of(db, pid) -> float:
    """The material thickness of the operation: what the Geometry tab shows (its input), else the saved value."""
    t = st.session_state.get("camming_thickness")
    return float(t if t else _saved(db, pid).get("thickness", 18.0))


def get_levels(db, pid, thickness: float | None = None) -> dict:
    """The levels of the operation (the later steps read them from here). upper = the top of the part: the material thickness of the
    Geometry step unless it was changed here; depth = how far the tool goes into the material from the upper level (the whole
    upper level unless it was changed)."""
    s = _saved(db, pid).get("levels", {})
    thk = float(thickness if thickness is not None else _saved(db, pid).get("thickness", 18.0))
    up = float(s["upper"]) if s.get("upper") is not None else thk
    return {"clearance": float(s.get("clearance", DEFAULTS["clearance"])), "safety": float(s.get("safety", DEFAULTS["safety"])),
            "upper": up, "depth": float(s["depth"]) if s.get("depth") is not None else up}


def validate(lv: dict) -> list[str]:
    p = []
    if not lv["safety"] >= 0:
        p.append("The safety distance cannot be negative.")
    if not lv["upper"] > 0:
        p.append("The upper level must be more than 0.")
    if not lv["depth"] > 0:
        p.append("The profile depth must be more than 0.")
    if lv["clearance"] <= lv["upper"] + lv["safety"]:
        p.append(f"The clearance level must be above the upper level plus the safety distance ({lv['upper'] + lv['safety']:g}).")
    return p


def step_levels(db, pid):
    ss = st.session_state
    thk = thickness_of(db, pid)
    saved = _saved(db, pid).get("levels", {})
    if ss.get("levels_loaded_pid") != pid:
        lv = get_levels(db, pid, thk)
        ss[PREFIX + "clearance"], ss[PREFIX + "safety"] = lv["clearance"], lv["safety"]
        ss[PREFIX + "upper"], ss[PREFIX + "depth"] = lv["upper"], lv["depth"]
        ss.levels_loaded_pid, ss.levels_thk, ss.levels_problems = pid, thk, []
    elif ss.get("levels_thk") != thk:                        # the thickness changed: what was never changed here follows it
        lv = get_levels(db, pid, thk)
        ss[PREFIX + "upper"], ss[PREFIX + "depth"] = lv["upper"], lv["depth"] if saved.get("depth") is not None else lv["upper"]
        ss.levels_thk = thk

    def _save():
        lv = {"clearance": float(ss[PREFIX + "clearance"]), "safety": float(ss[PREFIX + "safety"]),
              "upper": float(ss[PREFIX + "upper"]), "depth": float(ss[PREFIX + "depth"])}
        problems = validate(lv)
        if problems:
            ss.levels_problems = problems
            return
        try:
            cam = db.get_cam(pid) or {}
            cam["camming"] = {**cam.get("camming", {}), "levels": {
                "clearance": lv["clearance"], "safety": lv["safety"],
                "upper": None if lv["upper"] == thk else lv["upper"],           # None: always the geometry value
                "depth": None if lv["depth"] == lv["upper"] else lv["depth"]}}  # None: the whole upper level
            db.save_cam(pid, cam)
        except Exception as e:  # noqa: BLE001
            ss.levels_problems = [f"Could not save: {e}"]
            return
        ss.levels_problems = []
        ss.levels_msg = "Levels saved with the operation."

    st.caption("The Z levels of the toolpaths of this operation (Z = 0 is the bottom of the parts). They are saved with the operation.")
    msg = ss.pop("levels_msg", None)
    if msg:
        st.toast(msg, icon=":material/check_circle:")
    c, s_, u, d = (float(ss[PREFIX + k]) for k in ("clearance", "safety", "upper", "depth"))
    notes = [f"Rapid moves at Z = {c:g}",
             f"Feed Z speed starts at Z = {u + s_:g} (upper level + safety distance)",
             f"Top of the part. The default is the material thickness of the Geometry step ({thk:g})",
             f"The tool cuts down to Z = {u - d:g} (upper level − profile depth)" + ("  ·  below Z = 0, the bottom of the parts" if u - d < 0 else "")]
    with st.form("levels_form", border=False):             # nothing is sent until Save levels: typing never reruns the page
        for i, (label, key, step, hlp) in enumerate((
                ("Clearance level", "clearance", 1.0, "The Z level where the tool moves in rapid mode, above the parts, to go to another location."),
                ("Safety distance", "safety", 0.5, "The distance above the top of the part where the tool slows down from the rapid move to the feed Z speed."),
                ("Upper level", "upper", 0.5, "The top of the part. By default it is the material thickness of the Geometry step."),
                ("Profile depth", "depth", 0.5, "How far the tool goes into the material, measured down from the upper level. "
                                                "The upper level itself cuts the parts through to Z = 0."))):
            col, note = st.columns([1, 3])
            col.number_input(label, min_value=0.0, step=step, format="%.2f", key=PREFIX + key, help=hlp)
            note.markdown(f"<div style='padding-top:2.1rem'>{notes[i]}</div>", unsafe_allow_html=True)    # level with the box, not its label
        for p in ss.get("levels_problems", []):
            st.error(p)
        st.form_submit_button("Save", type="primary", on_click=_save, key="levels_save")
    if abs(u - thk) > 1e-9:
        st.caption(f"The upper level differs from the geometry value ({thk:g}). Type {thk:g} to use the geometry value again.")
