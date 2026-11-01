"""Is the app still trying? Which stopped jobs are "Applying" (a retry is coming) and which are "Couldn't apply" (it has given up).

A job whose latest run stopped (waiting_browser, timed_out, failed) used to read "Repairing" whatever happened next, so 21 jobs
sat there for days beside runners that were idle (2026-11-01). The answer is the retry code's own: nothing here is a second set of
rules.

  recovery.schedule_recovery   writes draft_recovery:<job> {"scheduled": true} for a stop it will resume
  autopilot.stalled_candidates the daily retry: the platforms it covers, PERSIST_SKIP blockers, the same stop 3 times in a row
                               (same_stall_streak), a stall repeated unchanged (recovery.repeats_unchanged)
  application_budget.allowance the starts a job may use (require_allowance): three, within the eight-minute deadline, and the
                               repeated-stop limit
  "failed" runs                no retry path starts them: only waiting_browser and timed_out are resumed

A retry that is only waiting for its time (a back-off, a busy worker or employer) is still coming, so it is "applying".
"""

from sqlalchemy import select

from .admission import QueueBlocked
from .db import Job, Run, Setting, latest_runs
from .vocabulary import RETRIED_STATES

RESUMED = ("waiting_browser", "timed_out")


def given_up(session, only=None):
    """Ids of the jobs whose newest run stopped and no automatic retry is coming."""
    from .application_budget import require_allowance
    from .autopilot import PERSIST_ATS, PERSIST_SKIP, SAME_STALL_RUNS, run_engines, same_stall_streak
    from .features.discovery.roles import role_fits
    from .recovery import repeats_unchanged
    from .schemas import Profile

    newest = latest_runs(*(() if only is None else (Run.job_id.in_(list(only)),)))
    stopped = session.execute(
        select(newest.c.job_id, newest.c.id, newest.c.state, Job.ats, Job.title, Job.status)
        .join(Job, Job.id == newest.c.job_id)
        .where(newest.c.state.in_(RETRIED_STATES), Job.demo.is_(False))
    ).all()
    if not stopped:
        return set()
    ids = [row.job_id for row in stopped]
    recoveries = {
        row.key.removeprefix("draft_recovery:"): row.value or {}
        for row in session.scalars(select(Setting).where(Setting.key.in_(["draft_recovery:" + i for i in ids])))
    }
    focus = set((session.get(Setting, "ats_focus").value if session.get(Setting, "ats_focus") else {}).get("exclude", []))
    profile = session.get(Setting, "profile")
    roles = Profile.model_validate(profile.value or {}).target_roles if profile else []
    history = {}
    for run in session.execute(
        select(Run.id, Run.job_id, Run.state, Run.reason, Run.created_at, Run.finished_at)
        .where(Run.job_id.in_(ids))
        .order_by(Run.created_at)
    ):
        history.setdefault(run.job_id, []).append(run)
    engines = run_engines(session, [run for runs in history.values() for run in runs])
    gone = set()
    for row in stopped:
        runs = history[row.job_id]
        latest = runs[-1]
        scheduled = recoveries.get(row.job_id, {})
        coming = bool(scheduled.get("scheduled") and scheduled.get("source_run_id") == row.id)
        if not coming:
            # The daily retry (autopilot.stalled_candidates), without its timing: a wait is still a retry coming.
            coming = (
                row.state in RESUMED
                and row.ats in PERSIST_ATS
                and row.ats not in focus
                and not PERSIST_SKIP.search(latest.reason or "")
                and not (roles and not role_fits(row.title, roles))
                and same_stall_streak(runs, engines) < SAME_STALL_RUNS
                and not repeats_unchanged((r.reason, engines.get(r.id)) for r in runs)
            )
        if coming:
            try:
                require_allowance(session, row.job_id)
            except QueueBlocked:
                coming = False
        if not coming:
            gone.add(row.job_id)
    return gone
