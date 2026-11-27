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


@st.fragment
def ops_order_editor(db, run_id, ops: list):
    """The order of the operations of the run: click a row to select an operation (only this part of the page reacts), move it with the
    buttons (the whole page is drawn again with the new order)."""
    ss = st.session_state
    ids = [o["id"] for o in ops]
    ver = ss.setdefault("ops_move_ver", 0)                   # a new table key after every move: the table is drawn again, the row highlighted
    state = ss.get(f"ops_ord_tbl_{ver}")
    rows = tuple(state["selection"]["rows"]) if state else ()
    if rows != ss.get("ops_ord_prev", ()):
        ss.ops_ord_prev = rows
        if rows and rows[0] < len(ids):
            ss.ops_move_sel = ids[rows[0]]
    if ss.get("ops_move_sel") not in ids:
        ss.ops_move_sel = None
    sel = ss.ops_move_sel
    st.caption("Click a row to select an operation, then move it with the buttons. The order is saved at once.")
    with st.container(key="ops_order_btns"):
        b1, b2, b3, b4 = st.columns(4)
    pressed = None
    for col, kind, label in ((b1, "up", "▲ Up"), (b2, "down", "▼ Down"), (b3, "first", "⤒ First"), (b4, "last", "⤓ Last")):
        if col.button(label, key=f"ops_move_{kind}", width="stretch", disabled=sel is None):
            pressed = kind
    if pressed:
        try:
            db.set_operation_order(run_id, moved_order(ids, sel, pressed))
        except Exception as e:  # noqa: BLE001
            st.error(f"Could not change the order: {e}")
            return
        ss.ops_move_ver += 1
        ss.ops_ord_prev = ()
        ss.ops_sel, ss.ops_prev_rows = None, ()                # nothing is open any more: the rows of the list have other operations now
        ss.ops_ver = ss.get("ops_ver", 0) + 1
        st.rerun()                                           # the whole page: the list of the operations above has a new order
    df = pd.DataFrame([{"No.": i, "Name": o["name"], "Sheet": (o["settings"] or {}).get("sheet", 1),
                        "Tool": (f"T{o['settings']['tool']}" if (o["settings"] or {}).get("tool") else "")} for i, o in enumerate(ops, 1)])
    hi = ids.index(sel) if sel in ids else -1
    styled = df.style.apply(lambda r: ["background-color: #cfe9f7; color: #0f3d57; font-weight: 600"] * len(r) if r.name == hi else [""] * len(r), axis=1)
    st.dataframe(styled, hide_index=True, width="content", on_select="rerun", selection_mode="single-row", key=f"ops_ord_tbl_{ver}")


def _open_cam_options():
    st.session_state.cam_options_open = True


def _open_autocam():
    st.session_state.autocam_open = True


def operations_tab(db, pid, result, project: str = "", meta: dict | None = None, run_id=None):
    ss = st.session_state
    if not run_id:
        b1, b2, _ = st.columns([1.5, 1.6, 4.9])
        b1.button("🤖 AutoCam", on_click=_open_autocam, key="autocam_btn", width="stretch")
        b2.button("⚙ CAM options", on_click=_open_cam_options, key="cam_options_btn", width="stretch",
                  help="Settings for all CAM operations: the tool library and the cutting data.")
        st.info("Nest first, or load a run: the operations belong to a stored run.")
        return
    if ss.get("ops_run") != run_id:                       # another run: nothing is open
        ss.ops_run, ss.ops_sel, ss.ops_prev_rows, ss.ops_ver, ss.ops_msg = run_id, None, (), ss.get("ops_ver", 0) + 1, None
    try:
        ops = [dict(r) for r in db.list_operations(run_id)]
    except Exception as e:  # noqa: BLE001
        st.error(f"Could not read the operations: {e}")
        return
    by_id = {o["id"]: o for o in ops}
    if ss.get("ops_sel") not in by_id:
        ss.ops_sel = None

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
        ss.ops_sel = new_id
        ss.ops_ver += 1
        ss.ops_prev_rows = ()

    def _start_create():
        ss.ops_creating = True
        ss.ops_error = None

    def _cancel_create():
        ss.ops_creating = False
        ss.ops_error = None

    def _close():
        ss.ops_sel = None
        ss.ops_ver += 1
        ss.ops_prev_rows = ()

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
        _close()

    st.caption("An operation is one machining job (one sheet of this run). Create it, give it a name, then open it to define its "
               "five steps. The G-code file gets the name of the operation.")
    opened = ss.get("ops_sel") is not None
    creating = bool(ss.get("ops_creating")) and not opened
    ss.ops_creating = creating
    cbtn, abtn, obtn, _ = st.columns([1.9, 1.5, 1.6, 3.0])
    cbtn.button("➕ Create operation", on_click=_start_create, key="ops_new", type="primary", width="stretch", disabled=opened or creating,
                help="Close the open operation first." if opened else None)
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
    df = pd.DataFrame([{"No.": i, "Name": o["name"], "Sheet": (o["settings"] or {}).get("sheet", 1),
                        "Tool": (f"T{o['settings']['tool']}" if (o["settings"] or {}).get("tool") else "")} for i, o in enumerate(ops, 1)])
    tleft, _ = st.columns([3, 5])                          # the table is as wide as the three buttons below it, whatever the names
    cfg = {"No.": st.column_config.NumberColumn(width=45), "Name": st.column_config.TextColumn(width=180),
           "Sheet": st.column_config.NumberColumn(width=55), "Tool": st.column_config.TextColumn(width=50)}     # long names are cut
    ev = tleft.dataframe(df, hide_index=True, width="stretch", column_config=cfg, on_select="rerun", selection_mode="single-row",
                         key=f"ops_table_{ss.ops_ver}")
    rows = tuple(ev.selection.rows)
    if rows != ss.ops_prev_rows:
        ss.ops_prev_rows = rows
        ss.ops_sel = ops[rows[0]]["id"] if rows else None
        ss.ops_error = None
        ss.ops_creating = False
        st.rerun()
    if len(ops) > 1:
        with st.expander("Order of the operations (move an operation up or down)"):
            ops_order_editor(db, run_id, ops)
    if ss.ops_sel is None:
        st.caption("Click an operation in the list to open it.")
        return
    op = by_id[ss.ops_sel]
    bleft, _ = st.columns([3, 5])                          # right under the table, the same width as the table
    with bleft.container(key="ops_btns"):                  # (the phone keeps the three buttons side by side)
        b1, b2, b3 = st.columns(3)
    b1.button("Close", on_click=_close, key="ops_close", width="stretch")
    with b2.popover("Rename", width="stretch"):
        st.text_input("New name", value=op["name"], key="ops_rename")
        st.button("Rename", on_click=_rename, key="ops_do_rename")
    with b3.popover("Delete", width="stretch"):
        st.write(f"Delete operation **{op['name']}** completely?")
        st.button("Yes, delete", on_click=_delete, key="ops_do_delete")
    st.subheader(f"Operation: {op['name']}")
    operation_view(db, op, result, project, meta, run_id)
