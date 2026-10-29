"""Close the stopped applications whose own stop reason says the posting is gone.

New runs are closed when they finish (service.completion.CLOSED_POSTING). This is the one-time pass over the runs that
stopped before that rule: MintMCP's Ashby page, Paylocity's "job does not exist" page and Oracle 404 pages sat in
Repairing and were retried (2026-10-29).
"""

from sqlalchemy import select

from ..db import Event, Job, Run, latest_runs, now
from ..service.completion import CLOSED_POSTING

STOPPED = ("waiting_browser", "timed_out", "failed")


def close_gone_postings(db):
    """Latest runs in a stopped state with a posting-gone reason become `closed`. Returns the run ids."""
    closed = []
    with db.exclusive() as s:
        latest = latest_runs()
        for run_id, job_id in s.execute(select(latest.c.id, latest.c.job_id).where(latest.c.state.in_(STOPPED))).all():
            run = s.get(Run, run_id)
            if not CLOSED_POSTING.search(run.reason or "") or run.receipt or run.packet.get("reservation_run_id"):
                continue
            run.state, run.finished_at = "closed", run.finished_at or now()
            run.reason = ("The posting is closed: " + (run.reason or ""))[:4000]
            if job := s.get(Job, job_id):
                job.status = "closed"
            s.add(Event(run_id=run_id, kind="posting_closed", message="The stop reason says the posting is gone", data={}))
            closed.append(run_id)
    return closed
