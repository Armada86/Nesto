"""CAM options, Cutting data: the defaults that AutoCam uses for the operations it makes (nobody types them there)."""
from __future__ import annotations

import streamlit as st

import autocam
import machine_profile as machine

SETTING = "cutting_defaults"
FIELDS = [   # (group, key, label, help, step)
    ("mill", "feed_xy", "Feed XY [mm/min]", "Cutting feed of the end mills along the contour.", 50.0),
    ("mill", "feed_z", "Feed Z [mm/min]", "Feed of the end mills going down (plunge).", 10.0),
    ("mill", "rpm", "Spin rate [rpm]", "Spindle speed of the end mills.", 500.0),
    ("drill", "feed", "Feed [mm/min]", "Feed of the drills (feed Z of the drilling cycle).", 10.0),
    ("drill", "rpm", "Spin rate [rpm]", "Spindle speed of the drills.", 500.0),
    ("drill", "peck", "Peck depth [mm]", "0 = drill straight through (G81); more than 0 = peck drilling (G83) with this depth per peck.", 1.0),
    ("drill", "extra", "Extra depth [mm]", "How far the drill goes below the bottom of the parts (0 = down to the bottom), so the hole is through.", 0.5),
    ("levels", "clearance", "Clearance level", "Height of the rapid moves.", 5.0),
    ("levels", "safety", "Safety distance", "The feed down starts this far above the top of the material.", 1.0),
]
PREFIX = "cd_"


def get_defaults(db) -> dict:
    try:
        return autocam.merged_defaults(db.get_app_setting(SETTING))
    except Exception:  # noqa: BLE001 - the saved values are optional
        return autocam.merged_defaults(None)


def validate(d: dict) -> list[str]:
    p = []
    m, dr, lv = d["mill"], d["drill"], d["levels"]
    for k, lab in (("feed_xy", "Feed XY"), ("feed_z", "Feed Z")):
        if not 0 < m[k] <= machine.MAX_FEED:
            p.append(f"Milling: {lab} must be more than 0 and at most {machine.MAX_FEED:g} mm/min.")
    if not 0 < m["rpm"] <= machine.MAX_RPM:
        p.append(f"Milling: the spin rate must be more than 0 and at most {machine.MAX_RPM:g} rpm.")
    if m["direction"] not in ("CW", "CCW"):
        p.append("Milling: the spin direction must be CW or CCW.")
    if not 0 < dr["feed"] <= machine.MAX_FEED:
        p.append(f"Drilling: the feed must be more than 0 and at most {machine.MAX_FEED:g} mm/min.")
    if not 0 < dr["rpm"] <= machine.MAX_RPM:
        p.append(f"Drilling: the spin rate must be more than 0 and at most {machine.MAX_RPM:g} rpm.")
    if dr["peck"] < 0 or dr["extra"] < 0:
        p.append("Drilling: the peck depth and the extra depth cannot be negative.")
    if lv["safety"] < 0 or lv["clearance"] <= 0:
        p.append("Levels: the clearance level must be more than 0 and the safety distance cannot be negative.")
    return p


def cutting_data_page(db):
    ss = st.session_state
    saved = get_defaults(db)
    st.caption("The cutting data AutoCam gives to every operation it makes. The material thickness is asked when AutoCam runs; the upper "
               "level and the profile depth follow it. You can still change any operation afterwards in its own steps.")

    def _fill():
        for g, k, _l, _h, _s in FIELDS:
            ss[f"{PREFIX}{g}_{k}"] = float(saved[g][k])
        ss[f"{PREFIX}mill_direction"] = saved["mill"]["direction"]

    if any(f"{PREFIX}{g}_{k}" not in ss for g, k, *_ in FIELDS) or f"{PREFIX}mill_direction" not in ss:      # forgotten (not drawn) or first time
        _fill()

    def _save():
        d = {g: dict(v) for g, v in saved.items()}
        for g, k, *_ in FIELDS:
            d[g][k] = float(ss[f"{PREFIX}{g}_{k}"])
        d["mill"]["direction"] = ss[f"{PREFIX}mill_direction"]
        problems = validate(d)
        if problems:
            ss.cd_problems = problems
            return
        try:
            db.save_app_setting(SETTING, d)
        except Exception as e:  # noqa: BLE001
            ss.cd_problems = [f"Could not save: {e}"]
            return
        ss.cd_problems = []
        ss.cd_msg = "Cutting data saved."

    msg = ss.pop("cd_msg", None)
    if msg:
        st.toast(msg, icon=":material/check_circle:")
    left, _ = st.columns([3, 2])
    with left.form("cutting_data_form", border=False):
        for p in ss.get("cd_problems", []):
            st.error(p)
        titles = {"mill": "End mills (contours and milled holes)", "drill": "Drills", "levels": "Levels"}
        for group in ("mill", "drill", "levels"):
            st.markdown(f"**{titles[group]}**")
            c1, c2 = st.columns(2)
            i = 0
            for g, k, lab, hlp, step in FIELDS:
                if g != group:
                    continue
                (c1 if i % 2 == 0 else c2).number_input(lab, min_value=0.0, step=step, key=f"{PREFIX}{g}_{k}", help=hlp)
                i += 1
            if group == "mill":
                (c1 if i % 2 == 0 else c2).radio("Spin direction", ["CW", "CCW"], key=f"{PREFIX}mill_direction", horizontal=True,
                                                 help="CW = M3 with climb milling.")
        st.form_submit_button("Save", type="primary", on_click=_save, key="cd_save")
