"""NestoCAM step 3 - Data: the cutting data of the operation (feeds, spindle speed and direction)."""
from __future__ import annotations

import streamlit as st

DEFAULTS = {"feed_xy": 2500.0, "feed_z": 100.0, "rpm": 10000.0, "direction": "CW"}
MAX_RPM = 15000.0          # the spindle of the Mariani (see machine_profile)
PREFIX = "dt_"


def get_data(db, pid) -> dict:
    """The saved cutting data of the operation (the defaults for what was never saved); the later steps read it from here."""
    try:
        saved = (db.get_cam(pid) or {}).get("camming", {}).get("data", {})
    except Exception:  # noqa: BLE001 - the saved data is optional
        saved = {}
    return {**DEFAULTS, **{k: saved[k] for k in DEFAULTS if k in saved}}


def validate(d: dict) -> list[str]:
    p = []
    for k, lab in (("feed_xy", "Feed XY"), ("feed_z", "Feed Z"), ("rpm", "Spin rate")):
        if not d[k] > 0:
            p.append(f"{lab} must be more than 0.")
    if d["rpm"] > MAX_RPM:
        p.append(f"Spin rate cannot be more than {MAX_RPM:g} rpm (the maximum of the spindle).")
    if d["direction"] not in ("CW", "CCW"):
        p.append("Choose the spin direction.")
    return p


def step_data(db, pid):
    st.caption("The cutting data used for the toolpaths of this operation. It is saved with the operation.")
    ss = st.session_state
    if ss.get("data_loaded_pid") != pid:
        for k, v in get_data(db, pid).items():
            ss[PREFIX + k] = v
        ss.data_loaded_pid = pid
        ss.data_problems = []

    def _save():
        d = {k: ss[PREFIX + k] for k in DEFAULTS}
        d = {**d, **{k: float(d[k]) for k in DEFAULTS if k != "direction"}}
        problems = validate(d)
        if problems:
            ss.data_problems = problems
            return
        try:
            cam = db.get_cam(pid) or {}
            cam["camming"] = {**cam.get("camming", {}), "data": d}
            db.save_cam(pid, cam)
        except Exception as e:  # noqa: BLE001
            ss.data_problems = [f"Could not save: {e}"]
            return
        ss.data_problems = []
        ss.data_msg = "Cutting data saved with the operation."

    msg = ss.pop("data_msg", None)
    if msg:
        st.toast(msg, icon=":material/check_circle:")
    col, _ = st.columns([1, 3])                              # one narrow list on the left
    with col, st.form("data_form", border=False):           # nothing is sent until Save data: typing never reruns the page
        st.number_input("Feed XY [mm/min]", min_value=0.0, step=50.0, format="%.0f", key=PREFIX + "feed_xy",
                        help="Cutting feed rate in the XY plane.")
        st.number_input("Feed Z [mm/min]", min_value=0.0, step=10.0, format="%.0f", key=PREFIX + "feed_z",
                        help="Feed rate of the plunge in Z.")
        st.number_input("Spin rate [rpm]", min_value=0.0, max_value=MAX_RPM, step=100.0, format="%.0f", key=PREFIX + "rpm",
                        help=f"Spindle speed. The spindle of the machine goes up to {MAX_RPM:g} rpm.")
        st.radio("Spin direction", ["CW", "CCW"], horizontal=True, key=PREFIX + "direction")
        for p in ss.get("data_problems", []):
            st.error(p)
        st.form_submit_button("Save", type="primary", on_click=_save, key="data_save")
