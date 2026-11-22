"""Nesting that keeps running when the browser goes away.

A Streamlit script stops when its browser connection closes (a phone that is locked, a tab that is put to sleep).
The polygon nesting can take minutes, so it runs in a background thread of the server instead. The job lives in a
registry shared by all sessions; the page shows its progress, and a page that is opened (or re-opened) later finds
the finished job, whose result was saved to the project history by the thread itself.
"""
from __future__ import annotations

import threading
import time
import uuid

from nesting import summary
from nesting_poly import PolyPart, nest_polygons
from shapes import part_geometry


class Cancelled(Exception):
    pass


class NestJob:
    def __init__(self, pid, nest_parts, meta):
        self.id = uuid.uuid4().hex
        self.pid = pid
        self.state = "running"          # running | done | error | cancelled
        self.frac = 0.0
        self.msg = "starting"
        self.started = time.time()
        self.elapsed = None
        self.result = None
        self.run_id = None
        self.info = {}
        self.nest_parts = nest_parts    # what was nested (for the cut list)
        self.meta = meta                # {"margin", "spacing"}
        self.error = None
        self.cancel = False


def run_job(job: NestJob, to_nest: list, cfg: dict, save) -> None:
    """Thread body. cfg: sheet_w, sheet_h, margin, spacing, rot_step, cell, time_limit.
    save(result, summary_dict, elapsed) stores the run and returns its id. Never touches Streamlit."""
    try:
        pp = [PolyPart(p["name"], part_geometry(p), p["qty"], p["rotate"], flip=p.get("flip", True)) for p in to_nest]

        def progress(f, msg):
            if job.cancel:
                raise Cancelled()
            job.frac, job.msg = min(max(float(f), 0.0), 1.0), str(msg)

        info = {}
        t0 = time.perf_counter()
        sheets = nest_polygons(pp, cfg["sheet_w"], cfg["sheet_h"], cfg["margin"], cfg["spacing"], cfg["rot_step"],
                               cfg["cell"], progress=progress, time_limit=cfg["time_limit"], info=info)
        elapsed = time.perf_counter() - t0
        result = [{"width": sh.width, "height": sh.height, "placements": [pl.__dict__ for pl in sh.placements]}
                  for sh in sheets]
        run_id = save(result, summary(sheets), elapsed)
        job.info, job.result, job.run_id, job.elapsed = info, result, run_id, elapsed
        job.frac, job.msg, job.state = 1.0, "done", "done"
    except Cancelled:
        job.state = "cancelled"
    except Exception as e:  # noqa: BLE001 - shown to the user
        job.error, job.state = str(e) or e.__class__.__name__, "error"


def start_job(job: NestJob, to_nest: list, cfg: dict, save) -> threading.Thread:
    th = threading.Thread(target=run_job, args=(job, to_nest, cfg, save), daemon=True, name=f"nest-{job.id[:6]}")
    th.start()
    return th
