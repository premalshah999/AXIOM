"""The next loop will not look like the last one: whatever the reason, one employer cannot keep burning runs.

TikTok and ByteDance, 2026-11-01: 47 runs in 40 minutes, none confirmed, each opening a browser to find what the one before
it found. A rule for that wording stops that wording. This stops the shape: an employer whose jobs keep ending without one
confirmation gets no more starts for a while, says so once as one item, and other employers carry on.

The numbers come from production's 5,718 started runs of 2026-10-18..11-01 (2,319 confirmed), replayed:

- NEW_EMPLOYER_JOBS, 5 different jobs in a day at an employer with no confirmation in two weeks. Counting jobs and not runs
  matters: a job's own retries are the job's problem (application_budget.job_ceiling_block), and counted as runs the same
  threshold stopped 10x the confirmations (5 runs: 94 confirmations in a hold at 36 employers; 5 jobs: 8 at 2).
- CONFIRMED_EMPLOYER_JOBS, 10 for an employer that has confirmed in those two weeks: it has shown it can work. Any
  confirmation or "already applied" clears the count, so an employer that keeps succeeding is never counted at all.
- SAME_STOP_JOBS, 6 jobs on one named stop, whichever employer. Below 5 it caught ordinary bad days (3: 94 confirmations in a
  hold, 4: 68). The engine's catch-all "did not advance" hides different causes, and a closed posting is the posting's own
  fact, so neither counts as the same stop; both still count toward the jobs above.

Replayed together: 9 employers stopped, 198 of 2,831 failed starts saved, 8 confirmations (0.3%) fell inside a hold, at 2
employers, and those were delayed, not lost. With a new build lifting a hold, as it does here, none did.
"""

from collections import defaultdict
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from .admission import QueueBlocked
from .application_budget import CATCH_ALL_STOP, stop_key
from .application_pacing import EMPLOYER_CAP_PREFIX, employer_cap_block, employer_identity
from .db import Job, Run, Setting
from .employer_limit import day_words

PREFIX = "loop_guard:"
NEW_EMPLOYER_JOBS = 5
CONFIRMED_EMPLOYER_JOBS = 10
SAME_STOP_JOBS = 6
AGAIN_JOBS = 2  # After a hold ends, what one more bad day needs: the wait doubles rather than the loop starting over.
WINDOW = timedelta(hours=24)
RECENT_CONFIRMATION = timedelta(days=14)
FIRST_WAIT = timedelta(hours=6)
LONGEST_WAIT = timedelta(days=7)
SUCCESS = {"confirmed", "already_applied", "dry_run_passed"}
# Stopped at the owner's hand or on the owner's question: no verdict on the employer, either way.
NO_VERDICT = {"queued", "running", "submitting", "cancelled", "paused", "needs_review", "waiting_answer"}
# The app's own trouble says nothing about the employer, and has its own gates (host_health, provider_health, bot_wall).
OWN_TROUBLE = (
    "The selected model provider is unavailable",
    "Worker interrupted",
    "Model budget is exhausted",
    "Open the ApplyPilot Chrome",
    "The local Chrome debugging",
    "Chrome worker tab",
    "The worker host was overloaded",
    "A platform bot check paused",
    "Browser observation stopped",
    "Replaced by a newer attempt",
)


def counts(state, reason, blocker=None):
    """Whether a finished run is one more failure at its employer."""
    if state in SUCCESS or state in NO_VERDICT or blocker == "ineligible":
        return False  # A posting that rules the applicant out is the posting's fact; the site worked.
    return not (reason or "").startswith(OWN_TROUBLE)


def verdict(rows, *, confirmed_recently=False, trips=0):
    """(jobs, top stop) when this employer's runs since its last success are a loop, else None.

    `rows`: oldest first, each with job_id, state, reason, blocker. Pure, so the replay and the engine share it."""
    jobs, named = set(), defaultdict(set)
    tally = defaultdict(int)
    for row in rows:
        if row["state"] in SUCCESS:
            jobs, named, tally = set(), defaultdict(set), defaultdict(int)
        elif counts(row["state"], row["reason"], row.get("blocker")):
            jobs.add(row["job_id"])
            key = stop_key(row["reason"])
            tally[row["reason"] or row["state"]] += 1
            if key and not key.startswith(CATCH_ALL_STOP) and row["state"] != "closed":
                named[key].add(row["job_id"])
    need = AGAIN_JOBS if trips else CONFIRMED_EMPLOYER_JOBS if confirmed_recently else NEW_EMPLOYER_JOBS
    if len(jobs) >= need or any(len(same) >= SAME_STOP_JOBS for same in named.values()):
        return len(jobs), max(tally, key=tally.get)
    return None


def _engine():
    from .runtime import engine_version

    return engine_version()


def _wait(trips):
    return min(LONGEST_WAIT, FIRST_WAIT * 2 ** max(0, trips - 1))


def _when(at):
    """"6:30 PM today"-free wording: the date and hour on this Mac's clock."""
    local = at.astimezone()
    return f"{day_words(local.isoformat())} at {local.strftime('%I:%M %p').lstrip('0')}"


def plain_stop(reason):
    """The run's reason as a clause: its first sentence, short."""
    first = " ".join(str(reason or "").split())
    for mark in (". ", "; "):
        first = first.split(mark)[0]
    return (first[:157].rstrip(" .,;:") + "...") if len(first) > 160 else first.rstrip(" .,;:")


def sentence(company, jobs, stop, until):
    name = " ".join((company or "").split()) or "This employer"
    return (f"{name}: the last {jobs} applications there all stopped without one going through (latest: "
            f"{plain_stop(stop)}), so its other jobs wait until {_when(until)}.")


def _rows_since(session, target, since):
    """This employer's started automatic runs since `since`, oldest first. Columns only at first: a saved packet is hundreds
    of KB, and only this employer's runs need theirs."""
    from .application_pacing import employer_keys
    from .features.knowledge.tables import KnAlias

    identity = employer_keys({a.alias: (a.employer_id, a.kind) for a in session.scalars(select(KnAlias))})
    from .admission import employer_partition

    found = []
    for rid, job_id, state, reason, started, company, url, ats in session.execute(
        select(Run.id, Run.job_id, Run.state, Run.reason, Run.started_at, Job.company, Job.url, Job.ats)
        .join(Job, Job.id == Run.job_id)
        .where(Run.started_at >= since.isoformat(), Run.mode == "submit", Job.demo.is_(False))
        .order_by(Run.started_at)
    ):
        key = identity(company, url) or "site:" + employer_partition({"url": url, "ats": ats, "company": company})
        if key == target:
            found.append({"id": rid, "job_id": job_id, "state": state, "reason": reason, "blocker": None})
    if found:
        packets = {
            rid: (blocker, verification, resumed)
            for rid, blocker, verification, resumed in session.execute(
                select(Run.id, Run.packet["blocker_type"].as_string(), Run.packet["verification_only"].as_boolean(),
                       Run.packet["email_wait_resume"].as_string()).where(Run.id.in_([r["id"] for r in found]))
            )
        }
        # A verification of an earlier submission, and the continuation of a matched email, are the attempt before them.
        found = [r for r in found if not packets.get(r["id"], (None, None, None))[1] and not packets.get(r["id"], (None, None, None))[2]]
        for row in found:
            row["blocker"] = packets.get(row["id"], (None,))[0]
    return found


def _confirmed_recently(session, target, at):
    from .application_pacing import employer_keys
    from .features.knowledge.tables import KnAlias
    from .admission import employer_partition

    identity = employer_keys({a.alias: (a.employer_id, a.kind) for a in session.scalars(select(KnAlias))})
    for company, url, ats in session.execute(
        select(Job.company, Job.url, Job.ats)
        .join(Run, Run.job_id == Job.id)
        .where(Run.state == "confirmed", Run.finished_at >= (at - RECENT_CONFIRMATION).isoformat(), Job.demo.is_(False))
    ):
        if (identity(company, url) or "site:" + employer_partition({"url": url, "ats": ats, "company": company})) == target:
            return True
    return False


def note_finished(session, run, job, state, reason=""):
    """After a run's end is recorded, in its transaction: a success clears the employer's count; a failure may stop it."""
    if job is None or job.demo or run.mode != "submit" or (run.packet or {}).get("verification_only"):
        return None
    target = employer_identity(session, job)
    key = PREFIX + target
    row = session.get(Setting, key)
    value = (row.value or {}) if row else {}
    if state in SUCCESS:
        if row:
            session.delete(row)
        return None
    if not counts(state, reason, (run.packet or {}).get("blocker_type")):
        return None
    at = datetime.now(UTC)
    if value.get("until", "") > at.isoformat() and value.get("engine") == _engine():
        return None  # Already held: a run that was under way ended; the hold says it once.
    # What an earlier build did says nothing about this one, as a hold's own lift on a new build says.
    current = value.get("engine") == _engine()
    trips = int(value.get("trips") or 0) if current else 0
    since = at - WINDOW
    for mark in (value.get("at") if current else None, value.get("released_at")):
        if mark:
            since = max(since, datetime.fromisoformat(mark))
    rows = _rows_since(session, target, since)
    # Cheap first: a trip needs at least this many jobs, whoever the employer is.
    least = AGAIN_JOBS if trips else NEW_EMPLOYER_JOBS
    if len({r["job_id"] for r in rows}) < least:
        return None
    found = verdict(rows, confirmed_recently=_confirmed_recently(session, target, at), trips=trips)
    if not found:
        return None
    jobs, stop = found
    until = at + _wait(trips + 1)
    fresh = {
        "until": until.isoformat(),
        "at": at.isoformat(),
        "trips": trips + 1,
        "company": job.company or "",
        "engine": _engine(),
        "jobs": jobs,
        "stop": plain_stop(stop),
        "source_run_id": run.id,
        "reason": sentence(job.company, jobs, stop, until),
    }
    if row:
        row.value = fresh
    else:
        session.add(Setting(key=key, value=fresh))
    return fresh


def active(session, target, at=None):
    """The loop hold in force for this employer, or None. Like a site circuit it holds only the build that tripped it: a
    new build may have fixed what stopped them."""
    row = session.get(Setting, PREFIX + target)
    value = (row.value or {}) if row else {}
    at = at or datetime.now(UTC)
    if value.get("until", "") > at.isoformat() and value.get("engine") == _engine():
        return value
    return None


WAIT_PREFIX = "employer_wait:"


def hold_site_wait(session, job, until, reason, circuit_key=""):
    """A wait the employer's site imposed (a lockout, a pacing notice, a spam refusal), for all of the employer's jobs under
    every link to them, and shown as one item. The site circuit and the cooldown it accompanies stay the site's own record."""
    if job is None or job.demo:
        return
    key = WAIT_PREFIX + employer_identity(session, job)
    value = {"until": until, "reason": reason, "company": job.company or "", "circuit_key": circuit_key}
    if row := session.get(Setting, key):
        row.value = value
    else:
        session.add(Setting(key=key, value=value))


def stop_of(session, job):
    """The QueueBlocked of whatever stops this employer as a whole right now (its stated limit, or the loop guard), or None.
    Whichever path asks: the batch, recovery, the repair agent, the owner's click. The owner's pick gets past the app's own
    filters, not an employer that has said no: pressing Apply would only open a browser to be told the same. The way past
    a wrong reading is Try again on the held employer (release)."""
    if job.demo:
        return None
    target = employer_identity(session, job)
    if blocked := employer_cap_block(session, target):
        return blocked
    if hold := active(session, target):
        return QueueBlocked(hold["reason"], outcome="deferred", code="loop_guard", not_before=hold["until"])
    wait = session.get(Setting, WAIT_PREFIX + target)
    if wait and (wait.value or {}).get("until", "") > datetime.now(UTC).isoformat():
        return QueueBlocked(wait.value.get("reason") or "This employer's site asked for a wait.", outcome="deferred", code="employer_wait", not_before=wait.value["until"])
    return None


def held_employers(session):
    """Every employer stopped as a whole, one item each: {key, kind, company, reason, until}."""
    out = []
    at = datetime.now(UTC)
    for row in session.scalars(select(Setting).where(Setting.key.startswith(EMPLOYER_CAP_PREFIX, autoescape=True))):
        value = row.value or {}
        if value.get("until", "") > at.isoformat():
            out.append({"key": row.key, "kind": "limit", "company": value.get("company", ""), "reason": value.get("sentence", ""), "until": value["until"]})
    for row in session.scalars(select(Setting).where(Setting.key.startswith(PREFIX, autoescape=True))):
        value = row.value or {}
        if value.get("until", "") > at.isoformat() and value.get("engine") == _engine():
            out.append({"key": row.key, "kind": "loop", "company": value.get("company", ""), "reason": value.get("reason", ""), "until": value["until"]})
    for row in session.scalars(select(Setting).where(Setting.key.startswith(WAIT_PREFIX, autoescape=True))):
        value = row.value or {}
        if value.get("until", "") > at.isoformat():
            out.append({"key": row.key, "kind": "site", "company": value.get("company", ""), "reason": value.get("reason", ""), "until": value["until"]})
    return sorted(out, key=lambda item: (item["company"].casefold(), item["key"]))


def release(session, key):
    """The owner's Try again: the employer's jobs may start now. A limit that was real comes back with the next refusal, a
    loop with the next AGAIN_JOBS failures (it keeps its count of trips until a confirmation clears it)."""
    if not key.startswith((PREFIX, EMPLOYER_CAP_PREFIX, WAIT_PREFIX)):
        return False
    row = session.get(Setting, key)
    if row is None:
        return False
    if key.startswith(WAIT_PREFIX) and (circuit := session.get(Setting, (row.value or {}).get("circuit_key") or "-")):
        session.delete(circuit)
    if key.startswith(PREFIX):
        at = datetime.now(UTC).isoformat()
        row.value = {**(row.value or {}), "until": at, "released_at": at}
    else:
        session.delete(row)
    return True


# How far back a refusal an earlier build closed a job over still counts.
LEARN_DAYS = 200


def learn_from_history(db):
    """At start: what the runs already recorded and no hold remembers. 33 of TikTok's jobs are closed on a refusal naming
    2026-12-31 and no build read it, so after the update its other jobs would each have opened one more browser. Only
    limits still in force are remembered, and only those not yet known. Returns how many."""
    from .application_pacing import remember_employer_cap
    from .employer_limit import APPLICATION_LIMIT, application_cap

    since = (datetime.now(UTC) - timedelta(days=LEARN_DAYS)).isoformat()
    learned = 0
    with db.exclusive() as s:
        rows = s.execute(
            select(Run.id, Run.reason, Run.finished_at, Job.id)
            .join(Job, Job.id == Run.job_id)
            .where(Run.finished_at >= since, Run.mode == "submit", Job.demo.is_(False), Run.reason.icontains("appl"), Run.reason.icontains("limit"))
            .order_by(Run.finished_at)
        ).all()
        for rid, reason, finished, job_id in rows:
            if not APPLICATION_LIMIT.search(reason or ""):
                continue
            cap = application_cap(reason)
            if not cap or not cap.get("until"):
                continue  # A wait this app would be guessing from an old run: the next refusal says.
            job = s.get(Job, job_id)
            if employer_cap_block(s, employer_identity(s, job)):
                continue
            if remember_employer_cap(s, job, cap, run_id=rid):
                learned += 1
    return learned
