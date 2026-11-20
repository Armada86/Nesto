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
