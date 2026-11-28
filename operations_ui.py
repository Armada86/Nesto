"""NestoCAM: the operations of a run. An operation is one machining job (one sheet): it is created first, gets a name, and holds
the five steps (Geometry, Tools, Data, Levels, Generate G-code). The G-code file has the name of the operation."""
from __future__ import annotations

import copy
import re

import pandas as pd
import streamlit as st

STEPS = [("geometry", "1 · Geometry"), ("tools", "2 · Tools"), ("data", "3 · Data"), ("levels", "4 · Levels"),
         ("gcode", "5 · Generate G-code")]
NEW_SETTINGS = {"sheet": 1, "thickness": 18.0}


def file_stem(name: str) -> str:
    """The G-code file name of an operation: its name, with only letters, digits, dot, dash and underscore."""
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._") or "operation"


class OpDB:
    """What the step tabs see as `db`: the tool library of the real db, and the settings of ONE operation as the
    `camming` part of get_cam / save_cam (so the steps do not need to know about operations)."""

    def __init__(self, db, op_id: int):
        self._db, self._op, self._cache = db, op_id, None

    def __getattr__(self, name):
        return getattr(self._db, name)

    def get_cam(self, _pid=None):
        if self._cache is None:
            row = self._db.get_operation(self._op)
            self._cache = copy.deepcopy((row or {}).get("settings") or {})
        return {"camming": copy.deepcopy(self._cache)}

    def save_cam(self, _pid, cam):
        self._cache = copy.deepcopy(cam.get("camming", {}))
        self._db.save_operation_settings(self._op, self._cache)


def operation_view(db, op: dict, result, project: str = "", meta: dict | None = None, run_id=None):
    """The five steps of one operation (op: {id, name, settings})."""
    from camming_ui import step_geometry
    from data_ui import step_data
    from gcode_ui import step_gcode
    from levels_ui import step_levels
    from tools_ui import step_tools
    odb = OpDB(db, op["id"])
    oid = op["id"]
    tabs = st.tabs([label for _k, label in STEPS])
    with tabs[0]:
        step_geometry(odb, oid, result)
    with tabs[1]:
        step_tools(odb, oid)
    with tabs[2]:
        step_data(odb, oid)
    with tabs[3]:
        step_levels(odb, oid)
    with tabs[4]:
        step_gcode(odb, oid, result, op["name"], project, meta, run_id)


def moved_order(ids: list, sel, kind: str) -> list:
    """The operation ids after moving `sel` one place up or down, or to the first or the last place."""
    new = list(ids)
    if sel not in new:
        return new
    i = new.index(sel)
    new.pop(i)
    j = {"up": max(0, i - 1), "down": min(len(new), i + 1), "first": 0, "last": len(new)}[kind]
    new.insert(j, sel)
    return new


def _open_cam_options():
    st.session_state.cam_options_open = True


def _open_autocam():
    st.session_state.autocam_open = True


def save_operation(db, op: dict, result) -> list[str]:
    """The Save of an open operation: the values of all its steps (levels, data, geometry) are written with the operation. Returns what is
    wrong ([] = everything is saved). The tool is saved when it is chosen, so there is nothing to save in that step."""
    from camming_ui import save_geometry
    from data_ui import save_data
    from levels_ui import save_levels
    odb, oid = OpDB(db, op["id"]), op["id"]
    problems = save_levels(odb, oid) + save_data(odb, oid)          # the levels first: they follow the thickness that is saved last
    if problems:
        return problems
    return save_geometry(odb, oid, result)


def forget_edits():
    """Drop what was typed in the steps of the operation that was open and not saved: the next operation (or this one again) is read
    from what is saved."""
    ss = st.session_state
    for k in ("data_loaded_pid", "levels_loaded_pid", "camming_loaded_pid", "geo_work_base"):
        ss[k] = None
    for k in [k for k in ss if str(k).startswith(("dt_", "lv_", "geo_pick_")) or k in ("camming_thickness", "camming_sheet")]:
        del ss[k]


def operations_tab(db, pid, result, project: str = "", meta: dict | None = None, run_id=None):
    ss = st.session_state
    if not run_id:
        b1, b2, _ = st.columns([1.5, 1.6, 4.9])
        b1.button("🤖 AutoCam", on_click=_open_autocam, key="autocam_btn", width="stretch")
        b2.button("⚙ CAM options", on_click=_open_cam_options, key="cam_options_btn", width="stretch",
                  help="Settings for all CAM operations: the tool library and the cutting data.")
        st.info("Nest first, or load a run: the operations belong to a stored run.")
        return
    if ss.get("ops_run") != run_id:                       # another run: nothing is selected, open or being reordered
        ss.ops_run, ss.ops_sel, ss.ops_open, ss.ops_reorder = run_id, None, None, False
        ss.ops_prev_rows, ss.ops_ver, ss.ops_msg = (), ss.get("ops_ver", 0) + 1, None
    try:
        ops = [dict(r) for r in db.list_operations(run_id)]
    except Exception as e:  # noqa: BLE001
        st.error(f"Could not read the operations: {e}")
        return
    by_id = {o["id"]: o for o in ops}
    ids = [o["id"] for o in ops]
    for k in ("ops_sel", "ops_open"):
        if ss.get(k) not in by_id:
            ss[k] = None
    if ss.get("ops_reorder") and (len(ops) < 2 or sorted(ss.get("ops_work") or []) != sorted(ids)):
        ss.ops_reorder = False                             # the operations changed under the reordering: start again

    def _clear_table():
        ss.ops_ver = ss.get("ops_ver", 0) + 1
        ss.ops_prev_rows = ()

    def _create():
        name = (ss.get("ops_new_name") or "").strip()
        if not name:
            ss.ops_error = "Give the operation a name."
            return
        try:
            start = copy.deepcopy(db.latest_operation_settings(pid) or NEW_SETTINGS)       # starts from the newest operation of the project
            if start.get("autocam"):                                    # one made by AutoCam: its tool, contours and drilling data are its own
                from cutting_data_ui import get_defaults
                import autocam
                d = get_defaults(db)
                start = {"sheet": start.get("sheet", 1), "thickness": start.get("thickness", 18.0), "data": dict(d["mill"]),
                         "levels": autocam.levels_for(d)}
            for key in ("contours", "kind", "drill", "autocam", "autocam_sheet", "order", "order_sheet"):   # a new operation cuts the whole sheet
                start.pop(key, None)
            start["sheet"] = min(int(start.get("sheet", 1)), max(1, len(result or [])))
            new_id = db.create_operation(run_id, name, start)
        except Exception as e:  # noqa: BLE001
            ss.ops_error = str(e)
            return
        ss.ops_error = None
        ss.ops_new_name = ""
        ss.ops_creating = False
        forget_edits()
        ss.ops_sel, ss.ops_open = None, new_id               # the new operation is opened
        _clear_table()

    def _start_create():
        ss.ops_creating = True
        ss.ops_error = None

    def _cancel_create():
        ss.ops_creating = False
        ss.ops_error = None

    def _open():
        forget_edits()                                         # what is shown is what is saved
        ss.ops_open, ss.ops_sel = ss.ops_sel, None
        ss.ops_save_problems = []
        _clear_table()

    def _discard():                                            # close the operation without saving what was changed
        forget_edits()
        ss.ops_open = None
        ss.ops_save_problems = []
        _clear_table()

    def _save_close():
        problems = save_operation(db, by_id[ss.ops_open], result)
        if problems:
            ss.ops_save_problems = problems                    # the operation stays open
            return
        ss.ops_msg = f"Operation {by_id[ss.ops_open]['name']} saved."
        forget_edits()
        ss.ops_open = None
        ss.ops_save_problems = []
        _clear_table()

    def _rename():
        name = (ss.get("ops_rename") or "").strip()
        if not name:
            ss.ops_error = "Give the operation a name."
            return
        try:
            db.rename_operation(ss.ops_sel, name)
            ss.ops_error = None
        except Exception as e:  # noqa: BLE001
            ss.ops_error = str(e)

    def _delete():
        try:
            db.delete_operation(ss.ops_sel)
        except Exception as e:  # noqa: BLE001
            ss.ops_error = f"Could not delete: {e}"
            return
        ss.ops_sel = None
        _clear_table()

    def _start_reorder():
        ss.ops_reorder, ss.ops_work, ss.ops_error = True, list(ids), None
        _clear_table()

    def _move(kind):
        ss.ops_work = moved_order(ss.ops_work, ss.ops_sel, kind)
        _clear_table()

    def _save_order():
        try:
            db.set_operation_order(run_id, ss.ops_work)
        except Exception as e:  # noqa: BLE001
            ss.ops_error = f"Could not change the order: {e}"
            return
        ss.ops_reorder = False
        ss.ops_msg = "The order of the operations is saved."
        _clear_table()

    def _cancel_order():
        ss.ops_reorder = False
        _clear_table()

    msg = ss.pop("ops_msg", None)
    if msg:
        st.toast(msg, icon=":material/check_circle:")
    st.caption("An operation is one machining job (one sheet of this run). Create it, give it a name, select it in the list and open it to "
               "define its five steps. The G-code file gets the name of the operation.")
    opened, reordering = ss.get("ops_open") is not None, bool(ss.get("ops_reorder"))
    creating = bool(ss.get("ops_creating")) and not opened and not reordering
    ss.ops_creating = creating
    cbtn, abtn, obtn, _ = st.columns([1.9, 1.5, 1.6, 3.0])
    cbtn.button("➕ Create operation", on_click=_start_create, key="ops_new", type="primary", width="stretch",
                disabled=opened or creating or reordering,
                help="Save or discard the open operation first." if opened else None)
    abtn.button("🤖 AutoCam", on_click=_open_autocam, key="autocam_btn", width="stretch",
                help="Finds the holes and outlines of a sheet, chooses the tools, makes the operations and the G-code by itself.")
    obtn.button("⚙ CAM options", on_click=_open_cam_options, key="cam_options_btn", width="stretch",
                help="Settings for all CAM operations: the tool library (create, change and delete tools) and the cutting data.")
    if creating:                                              # the name box only appears after the button is pressed
        left, _ = st.columns([3, 5])
        with left.form("ops_new_form", border=False):
            st.text_input("Name of the new operation", key="ops_new_name", placeholder="for example sheet_01_Milling")
            f1, f2 = st.columns(2)
            f1.form_submit_button("Create", type="primary", on_click=_create, key="ops_create", width="stretch")
            f2.form_submit_button("Cancel", on_click=_cancel_create, key="ops_cancel", width="stretch")
    if ss.get("ops_error"):
        st.error(ss.ops_error)

    if not ops:
        st.info("No operations in this run yet.")
        return
    st.markdown("**Operations of this run**")
    order = list(ss.ops_work) if reordering else ids
    shown = [by_id[i] for i in order]
    df = pd.DataFrame([{"No.": n, "Name": o["name"], "Sheet": (o["settings"] or {}).get("sheet", 1),
                        "Tool": (f"T{o['settings']['tool']}" if (o["settings"] or {}).get("tool") else "")} for n, o in enumerate(shown, 1)])
    tleft, _ = st.columns([5, 4])                          # the table is as wide as the buttons below it, whatever the names
    cfg = {"No.": st.column_config.NumberColumn(width=45), "Name": st.column_config.TextColumn(width=180),
           "Sheet": st.column_config.NumberColumn(width=55), "Tool": st.column_config.TextColumn(width=50)}     # long names are cut

    def _highlight(hi):
        return df.style.apply(lambda r: ["background-color: #cfe9f7; color: #0f3d57; font-weight: 600"] * len(r) if r.name == hi else [""] * len(r), axis=1)

    if opened:                                             # the table only shows which operation is open
        tleft.dataframe(_highlight(order.index(ss.ops_open) if ss.ops_open in order else -1), hide_index=True, width="stretch", column_config=cfg)
    else:                                                  # ONE table: select an operation; the buttons below say what to do with it
        data = _highlight(order.index(ss.ops_sel)) if (reordering and ss.ops_sel in order) else df
        ev = tleft.dataframe(data, hide_index=True, width="stretch", column_config=cfg, on_select="rerun", selection_mode="single-row",
                             key=f"ops_table_{ss.ops_ver}")
        rows = tuple(ev.selection.rows)
        if rows != ss.ops_prev_rows:
            ss.ops_prev_rows = rows
            if rows or not reordering:                     # (while reordering the chosen operation stays chosen when a click clears the row)
                ss.ops_sel = order[rows[0]] if rows else None
            ss.ops_error = None
            ss.ops_creating = False
            st.rerun()
    bleft, _ = st.columns([5, 4])                          # right under the table, the same width as the table
    sel = ss.ops_sel
    if opened:
        with bleft.container(key="ops_btns"):
            b1, b2 = st.columns(2)
        b1.button("Save", on_click=_save_close, key="ops_save", type="primary", width="stretch",
                  help="Saves everything of this operation (its steps) and closes it.")
        b2.button("Discard", on_click=_discard, key="ops_discard", width="stretch", help="Closes the operation without saving what you changed.")
        for p in ss.get("ops_save_problems", []):
            st.error(p)
        op = by_id[ss.ops_open]
        st.subheader(f"Operation: {op['name']}")
        operation_view(db, op, result, project, meta, run_id)
        return
    if reordering:
        with bleft.container(key="ops_order_btns"):
            m1, m2, m3, m4 = st.columns(4)
        for col, kind, label in ((m1, "up", "▲ Up"), (m2, "down", "▼ Down"), (m3, "first", "⤒ First"), (m4, "last", "⤓ Last")):
            col.button(label, on_click=_move, args=(kind,), key=f"ops_move_{kind}", width="stretch", disabled=sel is None)
        with bleft.container(key="ops_btns"):
            s1, s2 = st.columns(2)
        s1.button("Save", on_click=_save_order, key="ops_order_save", type="primary", width="stretch", help="Saves the new order of the operations.")
        s2.button("Cancel", on_click=_cancel_order, key="ops_order_cancel", width="stretch", help="Keeps the order as it was.")
        st.caption("Click an operation in the list, move it with the buttons, then Save." if sel is None else
                   f"Selected: **{by_id[sel]['name']}**. Move it with the buttons, then Save.")
        return
    with bleft.container(key="ops_btns"):                  # (the phone keeps the buttons side by side)
        b1, b2, b3, b4 = st.columns(4)
    b1.button("Open", on_click=_open, key="ops_open_btn", type="primary", width="stretch", disabled=sel is None)
    with b2.popover("Rename", width="stretch", disabled=sel is None):
        st.text_input("New name", value=by_id[sel]["name"] if sel else "", key="ops_rename")
        st.button("Rename", on_click=_rename, key="ops_do_rename")
    with b3.popover("Delete", width="stretch", disabled=sel is None):
        st.write(f"Delete operation **{by_id[sel]['name'] if sel else ''}** completely?")
        st.button("Yes, delete", on_click=_delete, key="ops_do_delete")
    b4.button("Reorder", on_click=_start_reorder, key="ops_reorder_btn", width="stretch", disabled=len(ops) < 2,
              help="Change the order of the operations of this run.")
    st.caption("Select an operation in the list, then press Open, Rename, Delete or Reorder." if sel is None else
               f"Selected: **{by_id[sel]['name']}**.")
