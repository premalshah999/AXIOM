"""One wall-clock deadline and two retries for an application's entire lifetime.

Queue time before the first browser start is free. Once started, waits, repairs,
deployments and verification continuations share the same eight minutes.

An owner-approved `application_budget_reset:<job>` setting ({"at": ISO time, "reason": ...}) ignores
the starts before its time, e.g. runs spent while the ATS itself was down for maintenance.

The wall-clock cap is the `application_budget` setting ({"max_seconds": n or null}); without the setting there is none
(owner, 2026-10-18: the cap was for monitored rollouts): each run ends at its own runner budget, and the start limit still
applies. It used to default to 480 s, which only a row written by hand switched off: of the 3,179 confirmed applications
on the one database that had it, 979 succeeded on a run started more than eight minutes after the job's first start
(2026-11-01), and a new installation would have refused every one of those retries.

A start that ended before anything reached the employer because of the app itself (a sibling held the shared email
sender, the browser or the model provider was unavailable, a deployment interrupted the worker) does not count toward
the start limit. Ford's Oracle application spent all three starts waiting for the shared sender, 16 s each.
"""
import re
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select

from .admission import QueueBlocked
from .db import Job, Run, Setting

MAX_SECONDS = 480
MAX_STARTS = 3
SETTING = "application_budget"
NOTHING_SENT_STATES = {"timed_out", "waiting_browser"}
# The worker Chrome could not open a page (browser.BrowserUnavailable). Three runs ended "The local Chrome debugging
# endpoint could not be reached" (2026-10-11) and were counted as starts because only the other two texts were listed.
BROWSER_OUTAGE = (
    "AXIOM's browser wasn't ready",  # browser.BROWSER_NOT_READY; the three below are what runs before 2026-11-01 stored.
    "Open the ApplyPilot Chrome window",
    "The local Chrome debugging endpoint could not be reached",
    # "... at attach_target (TimeoutError). No employer form was opened." (Michelin, HeyGen 2026-10-19)
    "Chrome worker tab initialization failed",
)
# A deadline timeout while the host's event loop was running late (host_health.host_overloaded_result): the clock ran
# out because the machine was overloaded, not because of the form. 70 runs on 2026-10-18/19 each lost one of three starts.
HOST_OVERLOADED = "The worker host was overloaded"
REGISTRATION_COOLDOWN = "A registration was already sent for this account"  # adapters.base.create_limit_result
NOTHING_SENT = (
    "Another application kept the same email sender busy",
    *BROWSER_OUTAGE,
    "The browser tab closed unexpectedly",
    "The selected model provider is unavailable",
    "Worker interrupted before submission",
    "A platform bot check paused",  # bot_wall.PAUSED
    HOST_OVERLOADED,
    REGISTRATION_COOLDOWN,
)


def nothing_sent(state, reason):
    """The run stopped on the app's own infrastructure before anything was sent to the employer."""
    return state in NOTHING_SENT_STATES and (reason or "").startswith(NOTHING_SENT)


# The same stop again and again. Across 3,563 runs (2026-10-24..31) a named stop (a field, an answer, a sign-in or page
# the engine could not get past) that the job had already ended on 5 times confirmed on the next try 4-7% of the time,
# against 15-17% for a first stop; 99 runs a week went to those, mostly on a refused sign-in or a missing email code.
# Every deploy gives a job a fresh three starts (allowance counts the current build only), so the same stop recurred
# build after build. The count here is not per build and not only back to back: Allen Institute's and ADP's stops
# alternated between two or three blockers. The engine's catch-all "did not advance" is exempt: it hides different
# causes and repeats no worse than a first try (12-16% at every count). Runs that sent nothing are exempt too (a model
# outage, Chrome unavailable, a worker interrupted), as are this gate's own refusals.
REPEAT_STOP_LIMIT = 5
REPEAT_STOP_SPACING = timedelta(hours=24)
CATCH_ALL_STOP = "the browser did not advance"
REPEAT_STOP_REFUSAL = "The same stop has now ended this application"
STOP_STATES = {"waiting_browser", "timed_out", "needs_review"}


def stop_key(reason):
    """A run's stop without the numbers (counts, field indexes, durations) that differ between identical stops."""
    return re.sub(r"\d+", "#", (reason or "").strip().lower())[:80]


def repeated_stop(session, job_id, *, aliases=None, reset_at=None):
    """(times, reason, last finished) when the job's newest named stop has ended it REPEAT_STOP_LIMIT times or more
    since the owner's last answer or reset, else None. Walks back from the newest run: runs that sent nothing and
    cancelled ones are skipped; a run that waited for the owner's answer, or ended any other way, ends the count."""
    rows = session.execute(
        select(Run.state, Run.reason, Run.created_at, Run.finished_at)
        .where(Run.job_id.in_(aliases or [job_id]), Run.mode == "submit")
        .order_by(Run.created_at.desc()).limit(60)
    ).all()
    stops, newest = [], None
    for state, reason, created, finished in rows:
        if state in {"cancelled", "queued", "running"} or nothing_sent(state, reason) or (reason or "").startswith(REPEAT_STOP_REFUSAL):
            continue
        instant = datetime.fromisoformat((created or "").replace("Z", "+00:00")) if created else None
        if instant is not None and instant.tzinfo is None:
            instant = instant.replace(tzinfo=UTC)
        if state not in STOP_STATES or (reset_at and instant and instant < reset_at):
            break
        key = stop_key(reason)
        if not key:
            break
        if newest is None:
            if key.startswith(CATCH_ALL_STOP):
                return None
            newest = (key, reason, finished or created)
        if key == newest[0]:
            stops.append(key)
    if len(stops) < REPEAT_STOP_LIMIT:
        return None
    return len(stops), newest[1], newest[2]


def max_seconds(session):
    """The lifetime wall-clock cap in seconds, or None when the owner runs production uncapped."""
    row = session.get(Setting, SETTING)
    if row is None:
        return None
    value = (row.value or {}).get("max_seconds", MAX_SECONDS)
    return None if value in (None, 0, "") else int(value)


def job_aliases(session, job_id):
    """The ids of every listing of the same posting on this platform (the job itself included)."""
    from .ats import icims_requisition_alias, url_identity

    job = session.get(Job, job_id)
    # url_identity is canonicalize(url)[1], cached: the uncached parse of every posting on the platform cost
    # 23 ms of each Apply click with 7,600 jobs (2026-10-22).
    identity = url_identity(job.url)
    return [row.id for row in session.execute(
        select(Job.id, Job.url, Job.ats, Job.company).where(Job.ats == job.ats)
    ) if url_identity(row.url) == identity or icims_requisition_alias(job, row)]


def budget_reset_at(session, aliases):
    """When the owner last gave these listings a fresh budget (Apply click, maintenance), or None."""
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
    return reset_at


def allowance(session, job_id, *, exclude_run=None, at=None, aliases=None):
    at = at or datetime.now(UTC)
    if aliases is None:
        aliases = job_aliases(session, job_id)
    from .runtime import engine_version

    current = engine_version()
    # Only starts on this engine build count: a failure on an older build says nothing about the current one, and the
    # owner wants fresh tries once a fix ships (2026-10-26, "Application already used its initial attempt and two
    # retries" held 40+ fixable jobs). The per-job daily model budget still bounds the cost.
    rows = session.execute(select(Run.id, Run.started_at, Run.state, Run.reason).where(
        Run.job_id.in_(aliases), Run.mode == "submit", Run.started_at.is_not(None),
        or_(Run.packet["engine_version"].as_string() == current, Run.packet["engine_version"].as_string().is_(None)),
    )).all()
    reset_at = budget_reset_at(session, aliases)
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


# Fresh tries the owner's answers alone can earn one job (an Apply click clears the count). An answer that is reused
# automatically for the same question could otherwise keep a failing job retrying for ever; the daily model budget
# bounds the cost, this bounds the number of tries.
ANSWER_TRIES = 6


def grant_fresh_try(session, job_id, reason, *, limit=ANSWER_TRIES):
    """The owner answered: earn this job a fresh try, as an Apply click does (owner, 2026-10-18: "give them fresh tries").

    onsemi and Mattel (2026-10-26/28) were answered, and the resume was refused for having used its three starts: the
    waiting_answer run itself is one of them. Returns False when answers alone have already earned `limit` of them.
    It only moves the start count and deadline; the duplicate-submission guards (an active, confirmed or uncertain run
    of the posting) are separate checks and still apply."""
    key = "application_budget_reset:" + job_id
    row = session.get(Setting, key)
    grants = int((row.value or {}).get("answer_grants", 0)) if row else 0
    try:
        recent = row and datetime.now(UTC) - datetime.fromisoformat(str(row.value["at"])) < timedelta(seconds=60)
    except (KeyError, ValueError):
        recent = False
    if recent and grants and row.value.get("reason") == reason[:200]:
        return True  # The click and the outbox wake both asked for this one answer: one grant.
    if grants >= limit:
        return False
    value = {"at": datetime.now(UTC).isoformat(), "reason": reason[:200], "answer_grants": grants + 1}
    if row:
        row.value = value
    else:
        session.add(Setting(key=key, value=value))
    return True


def repeat_stop_block(session, job_id, *, at=None, aliases=None):
    """A QueueBlocked when the job's newest named stop already ended it REPEAT_STOP_LIMIT times and the last of them
    is under a day old (one try a day after that), else None. The owner's Apply click resets the count."""
    at = at or datetime.now(UTC)
    aliases = aliases or job_aliases(session, job_id)
    found = repeated_stop(session, job_id, aliases=aliases, reset_at=budget_reset_at(session, aliases))
    if not found:
        return None
    times, reason, finished = found
    try:
        last = datetime.fromisoformat((finished or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    last = last.replace(tzinfo=UTC) if last.tzinfo is None else last
    if last + REPEAT_STOP_SPACING <= at:
        return None
    return QueueBlocked(
        f"{REPEAT_STOP_REFUSAL} {times} times ({(reason or '')[:140].rstrip('. ')}). It is retried once a day until "
        "something changes; applying again yourself starts it afresh",
        outcome="deferred", code="repeated_stop", not_before=(last + REPEAT_STOP_SPACING).isoformat(),
    )


def require_allowance(session, job_id, *, exclude_run=None, at=None, aliases=None, new_attempt=True):
    aliases = aliases or job_aliases(session, job_id)
    deadline, remaining, starts = allowance(
        session, job_id, exclude_run=exclude_run, at=at, aliases=aliases,
    )
    if remaining is not None and remaining <= 0:
        raise QueueBlocked("Application reached its eight-minute total deadline", code="application_deadline")
    if new_attempt and starts >= MAX_STARTS:
        raise QueueBlocked("Application already used its initial attempt and two retries", code="application_retry_limit")
    if new_attempt and (blocked := repeat_stop_block(session, job_id, at=at, aliases=aliases)):
        raise blocked
    return deadline, remaining
