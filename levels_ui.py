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
    """The material thickness of the project: what the Geometry tab shows (its input), else the saved value."""
    t = st.session_state.get("camming_thickness")
    return float(t if t else _saved(db, pid).get("thickness", 18.0))


def get_levels(db, pid, thickness: float | None = None) -> dict:
    """The levels of the project (the later steps read them from here). upper = the top of the material (the thickness, copied from the
    Geometry step); depth = how far the tool goes into the material from the upper level (the thickness unless it was changed)."""
    s = _saved(db, pid).get("levels", {})
    up = float(thickness if thickness is not None else _saved(db, pid).get("thickness", 18.0))
    return {"clearance": float(s.get("clearance", DEFAULTS["clearance"])), "safety": float(s.get("safety", DEFAULTS["safety"])),
            "upper": up, "depth": float(s["depth"]) if s.get("depth") is not None else up}


def validate(lv: dict) -> list[str]:
    p = []
    if not lv["safety"] >= 0:
        p.append("The safety distance cannot be negative.")
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
        ss[PREFIX + "clearance"], ss[PREFIX + "safety"], ss[PREFIX + "depth"] = lv["clearance"], lv["safety"], lv["depth"]
        ss.levels_loaded_pid, ss.levels_thk, ss.levels_problems = pid, thk, []
    elif ss.get("levels_thk") != thk:                        # the thickness changed: a profile depth that was never set follows it
        if saved.get("depth") is None:
            ss[PREFIX + "depth"] = thk
        ss.levels_thk = thk

    def _save():
        lv = {"clearance": float(ss[PREFIX + "clearance"]), "safety": float(ss[PREFIX + "safety"]), "upper": thk,
              "depth": float(ss[PREFIX + "depth"])}
        problems = validate(lv)
        if problems:
            ss.levels_problems = problems
            return
        try:
            cam = db.get_cam(pid) or {}
            cam["camming"] = {**cam.get("camming", {}), "levels": {"clearance": lv["clearance"], "safety": lv["safety"], "depth": lv["depth"]}}
            db.save_cam(pid, cam)
        except Exception as e:  # noqa: BLE001
            ss.levels_problems = [f"Could not save: {e}"]
            return
        ss.levels_problems = []
        ss.levels_msg = "Levels saved with the project."

    st.caption("The Z levels of the toolpaths of this project (Z = 0 is the bottom of the parts). They are saved with the project.")
    msg = ss.pop("levels_msg", None)
    if msg:
        st.toast(msg, icon=":material/check_circle:")
    col, info = st.columns([1, 3])
    with col, st.form("levels_form", border=False):         # nothing is sent until Save levels: typing never reruns the page
        st.number_input("Clearance level", min_value=0.0, step=1.0, format="%.2f", key=PREFIX + "clearance",
                        help="The Z level where the tool moves in rapid mode, above the parts, to go to another location.")
        st.number_input("Safety distance", min_value=0.0, step=0.5, format="%.2f", key=PREFIX + "safety",
                        help="The distance above the top of the part where the tool slows down from the rapid move to the feed Z speed.")
        st.number_input("Upper level", value=float(thk), format="%.2f", disabled=True,
                        help="The top of the part. It is copied from the material thickness of the Geometry step; change it there.")
        st.number_input("Profile depth", min_value=0.0, step=0.5, format="%.2f", key=PREFIX + "depth",
                        help="How far the tool goes into the material, measured down from the upper level. "
                             "The material thickness cuts the parts through to Z = 0.")
        for p in ss.get("levels_problems", []):
            st.error(p)
        st.form_submit_button("Save levels", type="primary", on_click=_save, key="levels_save")
    with info:
        c, s_, d = float(ss[PREFIX + "clearance"]), float(ss[PREFIX + "safety"]), float(ss[PREFIX + "depth"])
        st.markdown(f"**Rapid moves** at Z = {c:g}")
        st.markdown(f"**Feed Z speed starts** at Z = {thk + s_:g} (upper level + safety distance)")
        st.markdown(f"**The tool cuts down to** Z = {thk - d:g} (upper level − profile depth)")
        if thk - d < 0:
            st.caption("The tool goes below Z = 0, the bottom of the parts.")
