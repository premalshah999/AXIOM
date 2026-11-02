"""The success number the owner asked for (2026-10-31), made defensible for an investor (2026-11-02): of the jobs AXIOM was
attempting, how many were confirmed, with every exclusion named and every confirmation graded by its evidence.

Read-only and cheap: one pass over the `runs` rows of the window joined to their job's platform, never the `events` table
except for the commit of a job whose only proof is an employer's "already applied" page, then aggregated by success_core.py
(no database there, so the same rules score a saved dump). The rules and their reasons are in success_core.py; the queries
that give the same numbers on the live database are scripts/metrics/success.sql, one named query per figure.

A job is counted once, in the cohort of the window its first real run fell in, by its last run so far. Reported side by side: attempted; raw (confirmed / attempted);
filtered (confirmed / eligible, where eligible = attempted minus the jobs left out, each with a structured reason); first try
(confirmed by the job's first run, over the same eligible jobs; a run a stop of the app interrupted was no try); strict
(waiting-on-owner and in-progress jobs counted as misses); and the evidence behind the confirmations (strong, medium, weak).
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import aliased

from ..db import Event, Job, Run
from ..host_health import INTERRUPTED
from .success_core import NEEDS_OWN_COMMIT, TARGET, classify_jobs, evidence, pct, tally  # noqa: F401  (pct, TARGET: importers of the old module)

VOLUME = 8  # a platform with fewer tried jobs says too little to hold to the target
# Events that show AXIOM itself began a commit (a submit click or request), the only thing that makes an employer's
# "already applied" page one of AXIOM's applications. Page posts (`form_post`) are not: they also carry sign-ins and drafts.
COMMIT_KINDS = ("submitting", "submission", "submission_requests", "step_submit")
# Receipt fields the evidence grade reads, as success_core.evidence names them.
RECEIPT = {
    "type": ("type",),
    "web": ("website_confirmed",),
    "ean": ("existing_application_notice",),
    "res": ("reconciled_existing_submission",),
    "mea": ("matched_existing_application",),
    "sel": ("evidence_selector",),
    "mail": ("email", "message_id"),
    "trusted": ("email", "sender_trusted"),
    "auth": ("email", "authenticated"),
}


def first_commits(session, job_ids):
    """{job id: created_at of the job's earliest run that began a commit}: AXIOM's own recorded attempts at the job."""
    if not job_ids:
        return {}
    return dict(
        session.execute(
            select(Run.job_id, func.min(Run.created_at))
            .join(Event, Event.run_id == Run.id)
            .where(Event.kind.in_(COMMIT_KINDS), Run.job_id.in_(job_ids))
            .group_by(Run.job_id)
        ).all()
    )


def jobs_in_window(session, cutoff):
    """One classified dict per job first tried since `cutoff`, read from all its runs (success_core.classify_jobs).

    A weak receipt (an employer's "already applied" page, a quoted final page, a note) confirms a job only when AXIOM's own
    earlier attempt began a commit; otherwise the application was not ours and the job is "already on file" (2026-11-01: 64
    of 74 such existing-application confirmations had no commit of ours behind them). Stored results are not rewritten; only
    this count reads them so."""
    fields = list(RECEIPT)
    window = aliased(Run)  # a name of its own: a bare Run inside this query would correlate with the outer one
    rows = session.execute(
        select(Run.job_id, Job.ats, Run.state, Run.reason, Run.created_at, Run.packet["blocker_type"].as_string(),
               *(Run.receipt[path[0] if len(path) == 1 else path].as_string() for path in RECEIPT.values()))
        .join(Job, Job.id == Run.job_id)
        .where(Run.job_id.in_(select(window.job_id).where(window.created_at >= cutoff)), Job.demo.is_(False))
        .order_by(Run.job_id, Run.created_at, Run.id)
    ).all()
    plain = [
        dict(job=job, ats=ats, state=state, created=created, blocker=blocker, receipt=dict(zip(fields, values)),
             # Six of a night's 24 stopped runs were one restart of the app (2026-11-01): each read as a job's failed first try.
             interrupted=(reason or "") == INTERRUPTED)
        for job, ats, state, reason, created, blocker, *values in rows
    ]
    flagged = {r["job"] for r in plain if r["state"] == "confirmed" and evidence(r["receipt"])[1] in NEEDS_OWN_COMMIT}
    return classify_jobs(plain, first_commits(session, flagged), cutoff)


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
