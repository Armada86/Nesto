"""Background nesting: the job runs in a thread, saves the run itself, can be cancelled."""
import pathlib
import sys
import time
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from nest_job import NestJob, start_job

PARTS = [{"name": "t", "shape": "triangle", "width": 200.0, "height": 150.0, "qty": 4, "rotate": True, "flip": True, "outline": None},
         {"name": "c", "shape": "circle", "width": 120.0, "height": 120.0, "qty": 3, "rotate": True, "flip": True, "outline": None}]
CFG = dict(sheet_w=900.0, sheet_h=500.0, margin=5.0, spacing=3.0, rot_step=90.0, cell=6.0, time_limit=0.0)


def wait(job, seconds=60):
    t = time.time()
    while job.state == "running" and time.time() - t < seconds:
        time.sleep(0.05)


def test_job_runs_in_a_thread_saves_the_run_and_reports_done():
    saved = []
    job = NestJob(1, PARTS, {"margin": 5.0, "spacing": 3.0})

    def save(result, s, elapsed):
        saved.append((result, s, elapsed))
        return 42
    th = start_job(job, PARTS, CFG, save)
    assert th.daemon
    wait(job)
    assert job.state == "done" and job.run_id == 42 and job.frac == 1.0
    assert sum(len(sh["placements"]) for sh in job.result) == 7 and len(saved) == 1 and job.elapsed > 0
    assert job.info.get("runs", 0) >= 1


def test_job_can_be_cancelled_and_errors_are_reported():
    job = NestJob(1, PARTS, {})
    job.cancel = True                                          # cancelled before the first progress call
    start_job(job, PARTS, dict(CFG, time_limit=30.0), lambda *a: 1)
    wait(job)
    assert job.state == "cancelled" and job.result is None

    bad = NestJob(1, PARTS, {})
    start_job(bad, [dict(PARTS[0], width=5000.0, height=5000.0)], CFG, lambda *a: 1)    # does not fit the sheet
    wait(bad)
    assert bad.state == "error" and "fit" in bad.error
