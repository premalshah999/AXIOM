"""The success number the owner asked for (2026-10-31), made defensible for an investor (2026-11-02): of the jobs AXIOM was
attempting, how many were confirmed, with every exclusion named and every confirmation graded by its evidence.

Read-only and cheap: one pass over the light columns of the `runs` rows of the window joined to their job's platform, then
aggregated by success_core.py (no database there, so the same rules score a saved dump). The `events` table is read only for the
commit of a job whose proof is weak, and a run's packet only for the last run of a job still unconfirmed. The rules and their
reasons are in success_core.py; the query that gives the same numbers on the live database is scripts/metrics/success.sql.

A job is counted once, in the cohort of the window its first real run fell in, by its last run so far. Reported side by side: attempted; raw (confirmed / attempted);
filtered (confirmed / eligible, where eligible = attempted minus the jobs left out, each with a structured reason); first try
(confirmed by the job's first run, over the same eligible jobs; a run a stop of the app interrupted was no try); strict
(waiting-on-owner and in-progress jobs counted as misses); and the evidence behind the confirmations (strong, medium, weak).
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import aliased

from ..db import Event, Job, Run, bytewise
from ..host_health import INTERRUPTED
from .success_core import COMMIT_EVENT, TARGET, classify_jobs, fields, needs_own_commit, pct, tally  # noqa: F401  (pct: importers of the old module)

VOLUME = 8  # a platform with fewer tried jobs says too little to hold to the target


def first_commits(session, job_ids):
    """{job id: created_at of the job's earliest run that began a commit}: AXIOM's own recorded attempts at the job."""
    if not job_ids:
        return {}
    return dict(
        session.execute(
            select(Run.job_id, func.min(bytewise(Run.created_at)))
            .join(Event, Event.run_id == Run.id)
            .where(Event.kind == COMMIT_EVENT, Run.job_id.in_(job_ids))
            .group_by(Run.job_id)
        ).all()
    )


def jobs_in_window(session, cutoff):
    """One classified dict per job first tried since `cutoff`, read from all its runs (success_core.classify_jobs). A weak
    receipt confirms a job only when AXIOM's own commit came first (success_core.ours); stored results are not rewritten."""
    window = aliased(Run)  # a name of its own: a bare Run inside this query would correlate with the outer one
    rows = session.execute(
        select(Run.id, Run.job_id, Job.ats, Run.state, Run.created_at, Run.receipt, Run.reason)
        .join(Job, Job.id == Run.job_id)
        .where(Run.job_id.in_(select(window.job_id).where(window.created_at >= cutoff)), Job.demo.is_(False))
        .order_by(bytewise(Run.job_id), bytewise(Run.created_at), bytewise(Run.id))
    ).all()
    plain = [
        dict(run=run, job=job, ats=ats, state=state, created=created, receipt=fields(receipt) if state == "confirmed" else {},
             # Six of a night's 24 stopped runs were one restart of the app (2026-11-01): each read as a job's failed first try.
             interrupted=(reason or "") == INTERRUPTED)
        for run, job, ats, state, created, receipt, reason in rows
    ]
    flagged = {r["job"] for r in plain if r["state"] == "confirmed" and needs_own_commit(r["receipt"])}

    def blockers(run_ids):
        if not run_ids:
            return {}
        return dict(session.execute(select(Run.id, Run.packet["blocker_type"].as_string()).where(Run.id.in_(run_ids))).all())

    return classify_jobs(plain, first_commits(session, flagged), cutoff, blockers)


def success(session, days=7, now=None):
    """The overall success and each platform's, for the last `days` days: `{days, since, target, overall, platforms: [...]}`.
    `since` is the cutoff itself, so success.sql can repeat the count. Platforms carry `volume` (enough tried jobs to hold to the
    target) and the lowest final percentage comes first."""
    cutoff = ((now or datetime.now(UTC)) - timedelta(days=days)).isoformat()
    jobs = jobs_in_window(session, cutoff)
    by_platform = {}
    for j in jobs:
        by_platform.setdefault(j["ats"], []).append(j)
    platforms = [{"ats": ats, "volume": len(group) >= VOLUME, **tally(group)} for ats, group in by_platform.items()]
    # Below target first among platforms with volume, the worst at the top; then the rest by how much they tried.
    platforms.sort(key=lambda p: (not p["volume"], p["meets_target"], p["final_pct"] if p["final_pct"] is not None else 101, -p["tried"], p["ats"]))
    return {"days": days, "since": cutoff, "target": TARGET, "overall": tally(jobs), "platforms": platforms}
