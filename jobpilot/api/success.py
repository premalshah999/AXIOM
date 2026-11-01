"""The success number the owner asked for (2026-10-31): of the jobs AXIOM could honestly apply to, how many were confirmed.

Read-only and cheap: one pass over the `runs` rows of the window joined to their job's platform, never the `events` table, then
aggregated here, so it holds on any database and on synthetic rows in tests. The definitions are the ones the 90% push was
measured with (scratchpad/agents/land/score.sql), so this endpoint and that query agree on the same rows:

- tried: jobs with at least one real run (not a dry run) in the window;
- closed: the job's last run in the window ended closed or cancelled (a posting gone, a rule that drops the job, the owner's delete),
  and it was never confirmed;
- already applied: some run found the employer's own "already applied" (or a duplicate of a confirmed link), never confirmed;
- owner only: the last run is waiting on something only the owner can make (references, supervisors, a video, a work sample);
- eligible: tried, minus closed, minus already applied, minus owner only (a closed job that was confirmed anyway stays in);
- final: confirmed at any point in the window, over the eligible jobs; first try: confirmed by the job's first run, not counting
  a run a stop of the app interrupted before anything was sent: that was no try, and its job resumes for free.
"""

import re
from datetime import UTC, datetime, timedelta
from math import floor

from sqlalchemy import select

from ..db import Job, Run
from ..host_health import INTERRUPTED

TARGET = 90  # percent, per platform with volume and overall (the owner's goal)
VOLUME = 8  # a platform with fewer tried jobs says too little to hold to the target
CLOSED = {"closed", "cancelled"}
# Things only the owner can make; the engine can never answer them, so they're not an engine failure.
OWNER_ONLY = re.compile(r"supervisor|reference|video|writing sample|work sample|portfolio|publication", re.I)


def pct(part, whole):
    """A percentage to one decimal, or None with nothing to divide by. Never rounded up to a whole number: 89.6 is not 90."""
    return floor(1000 * part / whole) / 10 if whole else None


def jobs_in_window(session, cutoff):
    """{job id: facts} for every job with a run created since `cutoff`: the platform, whether any run confirmed or found the
    job already applied, the state of the first real run and the state and reason of the last."""
    rows = session.execute(
        select(Run.job_id, Job.ats, Run.state, Run.reason, Run.created_at)
        .join(Job, Job.id == Run.job_id)
        .where(Run.created_at >= cutoff, Job.demo.is_(False))
        .order_by(Run.job_id, Run.created_at, Run.id)
    )
    jobs = {}
    for job_id, ats, state, reason, _ in rows:
        j = jobs.setdefault(job_id, {"ats": ats, "ok": False, "dup": False, "first": None})
        j["ok"] |= state == "confirmed"
        j["dup"] |= state == "already_applied"
        # Six of a night's 24 stopped runs were one restart of the app (2026-11-01): each read as a job's failed first try.
        if j["first"] is None and state != "dry_run_passed" and (reason or "") != INTERRUPTED:
            j["first"] = state
        j["last"], j["why"] = state, reason or ""
    # A job whose only runs were dry runs was never tried; one only interrupted has its retry coming, and counts as tried.
    for j in jobs.values():
        if j["first"] is None and j["last"] != "dry_run_passed":
            j["first"] = j["last"]
    return [j for j in jobs.values() if j["first"] is not None]


def tally(jobs):
    closed = sum(j["last"] in CLOSED and not j["ok"] for j in jobs)
    duplicate = sum(j["dup"] and not j["ok"] for j in jobs)
    owner = sum(bool(OWNER_ONLY.search(j["why"])) and not j["ok"] and j["last"] not in CLOSED for j in jobs)
    open_jobs = sum(j["last"] not in CLOSED and not j["dup"] for j in jobs)
    confirmed = sum(j["ok"] for j in jobs)
    counted = open_jobs - owner  # the jobs the engine was expected to confirm
    final = pct(confirmed, counted)
    return {
        "tried": len(jobs),
        "closed": closed,
        "already_applied": duplicate,
        "owner_only": owner,
        "eligible": counted + sum(j["last"] in CLOSED and j["ok"] for j in jobs),
        "confirmed": confirmed,
        "final_pct": final,
        "first_try_pct": pct(sum(j["first"] == "confirmed" for j in jobs), counted),
        "meets_target": counted > 0 and 100 * confirmed >= TARGET * counted,
    }


def success(session, days=7, now=None):
    """The overall success and each platform's, for the last `days` days: `{days, target, overall, platforms: [...]}`. Platforms
    carry `volume` (enough tried jobs to hold to the target) and the lowest final percentage comes first."""
    cutoff = ((now or datetime.now(UTC)) - timedelta(days=days)).isoformat()
    jobs = jobs_in_window(session, cutoff)
    by_platform = {}
    for j in jobs:
        by_platform.setdefault(j["ats"], []).append(j)
    platforms = [{"ats": ats, "volume": len(group) >= VOLUME, **tally(group)} for ats, group in by_platform.items()]
    # Below target first among platforms with volume, the worst at the top; then the rest by how much they tried.
    platforms.sort(key=lambda p: (not p["volume"], p["meets_target"], p["final_pct"] if p["final_pct"] is not None else 101, -p["tried"], p["ats"]))
    return {"days": days, "target": TARGET, "overall": tally(jobs), "platforms": platforms}
