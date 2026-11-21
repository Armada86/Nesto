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
