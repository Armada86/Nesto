"""Database tests. They run only when TEST_DATABASE_URL points at a throw-away PostgreSQL database
(never use your real Neon database here)."""
import os
import sys
import pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
import pytest

URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL not set")


@pytest.fixture()
def db(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", URL)
    import db as _db
    _db.init_db()
    return _db


def _run(db, pid, n):
    db.save_run(pid, 2500, 1250, 5, 3, n, 0.5, [{"width": 2500, "height": 1250, "placements": []}], 1.5)


def test_delete_runs_only_touches_the_given_runs_of_that_project(db):
    a, b = db.create_project("del-test-A"), db.create_project("del-test-B")
    try:
        for _ in range(3):
            _run(db, a, 1)
        _run(db, b, 2)
        runs_a = [r["id"] for r in db.list_runs(a)]
        run_b = db.list_runs(b)[0]["id"]
        assert len(runs_a) == 3
        # delete two of A's runs; also pass B's run id - it must NOT be deleted through project A
        assert db.delete_runs(a, [runs_a[0], runs_a[1], run_b]) == 2
        assert [r["id"] for r in db.list_runs(a)] == [runs_a[2]]
        assert [r["id"] for r in db.list_runs(b)] == [run_b]
        assert db.delete_runs(a, []) == 0
        assert db.delete_runs(a, [runs_a[2]]) == 1 and db.list_runs(a) == []
    finally:
        db.delete_project(a)
        db.delete_project(b)


def test_rename_project_keeps_everything_and_refuses_duplicates(db):
    a, b = db.create_project("ren-test-A"), db.create_project("ren-test-B")
    try:
        db.save_parts(a, [{"name": "p", "shape": "rectangle", "width": 10.0, "height": 5.0, "qty": 2, "rotate": True}])
        db.save_settings(a, {"sheet_w": 3000.0})
        _run(db, a, 1)
        db.rename_project(a, "ren-test-A2")
        got = db.get_project(a)
        assert got["name"] == "ren-test-A2" and got["settings"] == {"sheet_w": 3000.0}      # settings kept
        assert len(db.load_parts(a)) == 1 and len(db.list_runs(a)) == 1                     # parts and runs kept
        # the duplicate check is case-insensitive and ignores the project being renamed
        assert db.project_exists("REN-TEST-B")
        assert db.project_exists("ren-test-b", exclude_id=a)
        assert not db.project_exists("ren-test-b", exclude_id=b)                            # renaming B to its own name is fine
        assert not db.project_exists("ren-test-A2", exclude_id=a)
        # even if the app check were bypassed, the database refuses a duplicate name
        with pytest.raises(Exception):
            db.rename_project(a, "ren-test-B")
        assert db.get_project(a)["name"] == "ren-test-A2"
    finally:
        db.delete_project(a)
        db.delete_project(b)


def test_parts_remember_the_flip_flag_and_default_to_on(db):
    pid = db.create_project("flip-test")
    try:
        db.save_parts(pid, [
            {"name": "a", "shape": "rectangle", "width": 10.0, "height": 5.0, "qty": 1, "rotate": True, "flip": False},
            {"name": "b", "shape": "rectangle", "width": 10.0, "height": 5.0, "qty": 1, "rotate": True, "flip": True},
            {"name": "c", "shape": "rectangle", "width": 10.0, "height": 5.0, "qty": 1, "rotate": True},   # no flip key: on
        ])
        got = {p["name"]: p["flip"] for p in db.load_parts(pid)}
        assert got == {"a": False, "b": True, "c": True}
        # rows that existed before the column was added get "on" from the column default
        with db.connect() as c:
            c.execute("INSERT INTO parts(project_id,name,width,height,qty) VALUES (%s,'old',1,1,1)", (pid,))
        assert {p["name"]: p["flip"] for p in db.load_parts(pid)}["old"] is True
    finally:
        db.delete_project(pid)


def test_migration_turns_flip_on_for_parts_saved_before_the_column_existed(db):
    pid = db.create_project("flip-migration")
    try:
        with db.connect() as c:
            c.execute("ALTER TABLE parts DROP COLUMN flip")                               # the table as it was before
            c.execute("INSERT INTO parts(project_id,name,width,height,qty) VALUES (%s,'legacy',2,2,3)", (pid,))
        db.init_db()                                                                      # what the app does on start
        parts = db.load_parts(pid)
        assert parts[0]["name"] == "legacy" and parts[0]["flip"] is True and parts[0]["qty"] == 3
    finally:
        db.delete_project(pid)


def test_save_run_returns_its_id_and_a_layout_can_be_updated_only_inside_its_project(db):
    a, b = db.create_project("edit-A"), db.create_project("edit-B")
    try:
        rid = db.save_run(a, 2500, 1250, 5, 3, 1, 0.5, [{"width": 2500, "height": 1250, "placements": []}], 1.0)
        assert isinstance(rid, int) and [r["id"] for r in db.list_runs(a)] == [rid]
        new = [{"width": 2500, "height": 1250, "placements": [{"part": "p", "x": 5, "y": 5, "w": 1, "h": 1}]}]
        assert db.update_run_result(b, rid, new) == 0                      # another project: refused
        assert db.load_run(rid)["result"][0]["placements"] == []
        assert db.update_run_result(a, rid, new) == 1
        assert db.load_run(rid)["result"][0]["placements"][0]["part"] == "p"
    finally:
        db.delete_project(a)
        db.delete_project(b)


def test_tool_library_and_project_machining_settings_round_trip(db):
    keep = db.list_tools()                                       # the library is shared: put it back afterwards
    pid = db.create_project("cam-test")
    try:
        db.save_tools([{"number": 2, "name": "FINISH", "diameter": 3.0, "flutes": 2, "rpm": 14000, "feed": 3000, "plunge": 1000,
                        "step_down": 3.0, "coolant": "none"},
                       {"number": 1, "name": "EM 6", "diameter": 6.0, "flutes": 2, "rpm": 12000, "feed": 4000, "plunge": 1500,
                        "step_down": 6.0, "coolant": "flood"}])
        tools = db.list_tools()
        assert [t["number"] for t in tools] == [1, 2] and tools[0]["coolant"] == "flood" and tools[1]["diameter"] == 3.0
        assert db.get_cam(pid) is None
        db.save_cam(pid, {"thickness": 18.0, "tabs": 2, "climb": True})
        assert db.get_cam(pid) == {"thickness": 18.0, "tabs": 2, "climb": True}
        db.save_tools([])
        assert db.list_tools() == []
    finally:
        db.save_tools(keep)
        db.delete_project(pid)


def _tool(n, **kw):
    t = {"number": n, "tool_type": "END MILL", "name": "t", "diameter": 12.7, "shoulder_diameter": 12.7, "arbor_diameter": 12.7,
         "shoulder_angle": 0.0, "cutting_length": 34.8, "shoulder_length": 43.7, "total_length": 43.7, "chamfer": 0.0,
         "outside_holder": 122.2, "helical_angle": 30.0, "flutes": 3, "holder": "ADAPTOR"}
    t.update(kw)
    return t


def test_tool_library_add_change_renumber_delete(db):
    for r in db.list_cam_tools():
        db.delete_cam_tool(r["number"])
    db.save_cam_tool(_tool(1))
    db.save_cam_tool(_tool(2, diameter=6.0))
    assert [r["number"] for r in db.list_cam_tools()] == [1, 2]
    with pytest.raises(ValueError):
        db.save_cam_tool(_tool(2), None)                    # a new tool with a used number
    with pytest.raises(ValueError):
        db.save_cam_tool(_tool(2), 1)                       # tool 1 renumbered onto tool 2
    db.save_cam_tool(_tool(1, diameter=10.0), 1)                 # change in place
    assert next(r for r in db.list_cam_tools() if r["number"] == 1)["diameter"] == 10.0
    db.save_cam_tool(_tool(5, diameter=10.0), 1)            # renumber 1 -> 5
    assert [r["number"] for r in db.list_cam_tools()] == [2, 5]
    db.delete_cam_tool(2)
    db.delete_cam_tool(5)
    assert db.list_cam_tools() == []


def test_old_machining_tools_are_deleted_once(db):
    import db as _db
    with _db.connect() as c:
        c.execute("DELETE FROM app_migrations WHERE name='purge_old_cam_tools'")
        c.execute("INSERT INTO cam_tools(number,name,diameter,flutes,rpm,feed,plunge,step_down,coolant) VALUES (91,'old',6,2,1,1,1,1,'none')")
    _db.init_db()
    assert _db.list_tools() == []
    with _db.connect() as c:
        c.execute("INSERT INTO cam_tools(number,name,diameter,flutes,rpm,feed,plunge,step_down,coolant) VALUES (92,'kept',6,2,1,1,1,1,'none')")
    _db.init_db()                                           # the purge does not run again
    assert [t["number"] for t in _db.list_tools()] == [92]
    with _db.connect() as c:
        c.execute("DELETE FROM cam_tools")


def test_cam_operations_belong_to_a_run_have_unique_names_and_go_with_the_run(db):
    pid = db.create_project("ops-test")
    try:
        r1 = db.save_run(pid, 2500, 1250, 5, 3, 1, 0.5, [{"width": 2500, "height": 1250, "placements": []}], 1.5)
        r2 = db.save_run(pid, 2500, 1250, 5, 3, 1, 0.5, [{"width": 2500, "height": 1250, "placements": []}], 1.5)
        a = db.create_operation(r1, "sheet_01_Milling", {"sheet": 1, "thickness": 15.0})
        db.create_operation(r2, "sheet_01_Milling", {"sheet": 1})                       # the same name in another run is fine
        with pytest.raises(ValueError):
            db.create_operation(r1, "SHEET_01_milling", {})                                # not twice in one run (any capitals)
        assert [o["name"] for o in db.list_operations(r1)] == ["sheet_01_Milling"]
        db.save_operation_settings(a, {"sheet": 2, "tool": 1})
        assert db.get_operation(a)["settings"] == {"sheet": 2, "tool": 1}
        b = db.create_operation(r1, "second", {})
        with pytest.raises(ValueError):
            db.rename_operation(b, "Sheet_01_milling")
        db.rename_operation(b, "third")
        assert db.latest_operation_settings(pid) is not None
        db.delete_operation(b)
        assert [o["name"] for o in db.list_operations(r1)] == ["sheet_01_Milling"]
        db.delete_runs(pid, [r1])                                                          # the operations go with their run
        assert db.list_operations(r1) == [] and len(db.list_operations(r2)) == 1
    finally:
        with db.connect() as c:
            c.execute("DELETE FROM projects WHERE id=%s", (pid,))


def test_a_run_keeps_its_nesting_options(db):
    pid = db.create_project("opts-test")
    try:
        r1 = db.save_run(pid, 2500, 1250, 5, 3, 1, 0.5, [{"width": 2500, "height": 1250, "placements": []}], 1.5,
                         options={"rot_label": "15°", "prec_label": "Fine", "effort_label": "Thorough"})
        r2 = db.save_run(pid, 2500, 1250, 5, 3, 1, 0.5, [{"width": 2500, "height": 1250, "placements": []}], 1.5)
        assert db.load_run(r1)["options"] == {"rot_label": "15°", "prec_label": "Fine", "effort_label": "Thorough"}
        assert db.load_run(r2)["options"] is None                                      # an older run: no options
    finally:
        with db.connect() as c:
            c.execute("DELETE FROM projects WHERE id=%s", (pid,))
