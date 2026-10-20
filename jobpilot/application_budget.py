"""One wall-clock deadline and two retries for an application's entire lifetime.

Queue time before the first browser start is free. Once started, waits, repairs,
deployments and verification continuations share the same eight minutes.

An owner-approved `application_budget_reset:<job>` setting ({"at": ISO time, "reason": ...}) ignores
the starts before its time, e.g. runs spent while the ATS itself was down for maintenance.

The wall-clock cap is the `application_budget` setting ({"max_seconds": n or null}); without the setting it is
480 s. Production runs uncapped (owner, 2026-10-18: the cap was for monitored rollouts): each run then ends at its
own runner budget, and the start limit still applies.

A start that ended before anything reached the employer because of the app itself (a sibling held the shared email
sender, the browser or the model provider was unavailable, a deployment interrupted the worker) does not count toward
the start limit. Ford's Oracle application spent all three starts waiting for the shared sender, 16 s each.
"""
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from .admission import QueueBlocked
from .db import Job, Run, Setting

MAX_SECONDS = 480
MAX_STARTS = 3
SETTING = "application_budget"
NOTHING_SENT_STATES = {"timed_out", "waiting_browser"}
# The worker Chrome could not open a page (browser.BrowserUnavailable). Three runs ended "The local Chrome debugging
# endpoint could not be reached" (2026-10-11) and were counted as starts because only the other two texts were listed.
BROWSER_OUTAGE = (
    "Open the ApplyPilot Chrome window",
    "The local Chrome debugging endpoint could not be reached",
    # "... at attach_target (TimeoutError). No employer form was opened." (Michelin, HeyGen 2026-10-19)
    "Chrome worker tab initialization failed",
)
# A deadline timeout while the host's event loop was running late (host_health.host_overloaded_result): the clock ran
# out because the machine was overloaded, not because of the form. 70 runs on 2026-10-18/19 each lost one of three starts.
HOST_OVERLOADED = "The worker host was overloaded"
NOTHING_SENT = (
    "Another application kept the same email sender busy",
    *BROWSER_OUTAGE,
    "The browser tab closed unexpectedly",
    "The selected model provider is unavailable",
    "Worker interrupted before submission",
    "A platform bot check paused",  # bot_wall.PAUSED
    HOST_OVERLOADED,
)


def nothing_sent(state, reason):
    """The run stopped on the app's own infrastructure before anything was sent to the employer."""
    return state in NOTHING_SENT_STATES and (reason or "").startswith(NOTHING_SENT)


def max_seconds(session):
    """The lifetime wall-clock cap in seconds, or None when the owner runs production uncapped."""
    row = session.get(Setting, SETTING)
    if row is None:
        return MAX_SECONDS
    value = (row.value or {}).get("max_seconds", MAX_SECONDS)
    return None if value in (None, 0, "") else int(value)


def allowance(session, job_id, *, exclude_run=None, at=None, aliases=None):
    at = at or datetime.now(UTC)
    job = session.get(Job, job_id)
    if aliases is None:
        from .ats import canonicalize, icims_requisition_alias

        identity = canonicalize(job.url)[1]
        aliases = [row.id for row in session.execute(
            select(Job.id, Job.url, Job.ats, Job.company).where(Job.ats == job.ats)
        ) if canonicalize(row.url)[1] == identity or icims_requisition_alias(job, row)]
    rows = session.execute(select(Run.id, Run.started_at, Run.state, Run.reason).where(
        Run.job_id.in_(aliases), Run.mode == "submit", Run.started_at.is_not(None),
    )).all()
    reset_at = None
    for value in session.scalars(
        select(Setting.value).where(Setting.key.in_(["application_budget_reset:" + a for a in aliases]))
    ):
        try:
            instant = datetime.fromisoformat(str((value or {}).get("at", "")).replace("Z", "+00:00"))
        except ValueError:
            continue
        instant = instant.replace(tzinfo=UTC) if instant.tzinfo is None else instant
        reset_at = max(reset_at or instant, instant)
    starts = []
    for rid, started, state, reason in rows:
        if not started:
            continue
        instant = datetime.fromisoformat(started.replace("Z", "+00:00"))
        instant = instant.replace(tzinfo=UTC) if instant.tzinfo is None else instant
        if reset_at and instant < reset_at:
            continue
        starts.append((rid, instant, nothing_sent(state, reason)))
    cap = max_seconds(session)
    deadline = None if cap is None else min((t for _, t, _ in starts), default=at) + timedelta(seconds=cap)
    count = sum(rid != exclude_run and not free for rid, _, free in starts)
    keys = ["validation_retry:" + rid for rid, _, _ in starts]
    if keys:
        count += sum(value.get("count", 0) for value in session.scalars(
            select(Setting.value).where(Setting.key.in_(keys))
        ))
    return deadline, None if deadline is None else max(0.0, (deadline - at).total_seconds()), count


def require_allowance(session, job_id, *, exclude_run=None, at=None, aliases=None, new_attempt=True):
    deadline, remaining, starts = allowance(
        session, job_id, exclude_run=exclude_run, at=at, aliases=aliases,
    )
    if remaining is not None and remaining <= 0:
        raise QueueBlocked("Application reached its eight-minute total deadline", code="application_deadline")
    if new_attempt and starts >= MAX_STARTS:
        raise QueueBlocked("Application already used its initial attempt and two retries", code="application_retry_limit")
    return deadline, remaining
