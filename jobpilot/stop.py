"""The one Stop (owner, 2026-11-01: "could not stop applications"; they quit, reopened and deleted jobs one by one while runs
kept starting). `control.paused` used to mean only "no new start": running applications carried on, the Overview's Resume and
`/autopilot` lifted it without a word, and queued work could not tell a stop from a pause.

Stop now means, wherever it was pressed (the top of every screen, Telegram, Quit):

- nothing new starts, from any path. Every path that can start an application goes through one of three gates, all read
  under the app lock, so a press and a start are ordered: `refuse` in queue admission (no run is even written), the claim in
  `_execute_owned` (queued -> running), and `reserve_submission` (running -> submitting);
- `outbox.claim` keeps the deliveries that lead to a start (a matched email, an answered question, a repair) unclaimed, so
  they are neither run nor spent against their retry limit, and are delivered after Resume;
- a running application is cancelled by `job_control.watch` within a second, closes its tabs and goes back to the queue. It
  sent nothing, so it isn't a failure, isn't a repair, spends no start and isn't on the score (`return_to_queue`);
- an application past `reserve_submission` is left alone: cutting a commit is what makes an unknown result. Its own ending
  is recorded as usual;
- it is a row in the database, so it holds across quit, reopen, crash and install. Only `resume` lifts it: nothing else
  writes `paused: false`.
"""

from datetime import UTC, datetime

from sqlalchemy import func, select

from .db import Event, Job, Run, Setting, now

# The start of what a stopped run says. application_budget.NOTHING_SENT lists it: a start that ends here is free.
STOPPED = "Stopped by you before anything was sent"
RETURNED = STOPPED + "; it is back in the queue and continues when you press Resume"
REFUSED = "AXIOM is stopped. This application starts when you press Resume"
# What the screen promises: a run's tabs are shut within this many seconds of the press (watch polls each second).
CLOSE_SECONDS = 15
# A commit is never cut, so this is what is said, not a limit: 95% of 2,248 commits (2026-10-20..11-01) ended within 147 s,
# and it is the same three minutes a server stop waits for one (SHUTDOWN_DRAIN_SECONDS).
FINISH_SECONDS = 180


class Stopped(ValueError):
    """A commit was refused because the owner pressed Stop. Nothing was sent; the application goes back to the queue."""

    def __init__(self):
        super().__init__(STOPPED)


def stopped_result():
    """The run result for an application stopped before its commit (service.execution returns it to the queue)."""
    return {"state": "stopped", "reason": RETURNED, "writer_released": True}


def stopped(db):
    return bool((db.get_setting("control", {}) or {}).get("paused"))


def stopped_in(session):
    """Inside a transaction: `stopped` as the app lock orders it."""
    row = session.get(Setting, "control")
    return bool(row and (getattr(row, "value", None) or {}).get("paused"))


def refuse(session):
    """Queue admission's gate: during a stop no run is written. A deferred refusal, so what asked keeps its place: an owner's
    click is remembered and starts after Resume (owner_apply), a recovery stays scheduled (retry_due), an email stays
    undelivered (outbox)."""
    if stopped_in(session):
        from .admission import QueueBlocked, temporary_time

        raise QueueBlocked(REFUSED, outcome="deferred", code="stopped", not_before=temporary_time())


def press(db, *, by="owner"):
    """Stop. Under the app lock, so the moment it commits no claim or reservation can begin. Pressing twice keeps the first
    press's clock."""
    with db.exclusive() as s:
        row = s.get(Setting, "control")
        value = dict(row.value or {}) if row else {"auto_submit": False}
        fresh = not value.get("paused")
        if fresh:
            value.update(paused=True, stopped_at=now(), stopped_by=by)
            if row:
                row.value = value
            else:
                s.add(Setting(key="control", value=value))
    if fresh:
        db.event("", "owner_stop", "Stop pressed: nothing new starts until Resume", {"by": by})
    return fresh


def lift(db, *, by="owner"):
    """The write Resume makes. `resume` also delivers what waited."""
    with db.exclusive() as s:
        row = s.get(Setting, "control")
        value = dict(row.value or {}) if row else {"auto_submit": False}
        fresh = bool(value.get("paused"))
        value = {k: v for k, v in value.items() if k not in {"stopped_at", "stopped_by"}} | {"paused": False}
        if row:
            row.value = value
        else:
            s.add(Setting(key="control", value=value))
    if fresh:
        db.event("", "owner_resume", "Resume pressed: applications continue", {"by": by})
    return fresh


async def resume(service, *, by="owner"):
    """The only way out of a stop: an explicit press. Delivers every queued run again (each ended its delivery when it found
    the stop) and wakes recoveries and the batch at once rather than at the next minute's tick."""
    import asyncio

    await asyncio.to_thread(lift, service.db, by=by)
    if service.dispatch:
        from .workers import enqueue_matching, resume as redeliver

        await redeliver(service)
        try:
            from .recovery import retry_due

            await retry_due(service)
            await enqueue_matching()
        except Exception:
            pass  # The next minute's tick does the same; the stop is already lifted.
    return status(service.db)


def _seconds_since(stamp, at):
    try:
        moment = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    return (at - (moment if moment.tzinfo else moment.replace(tzinfo=UTC))).total_seconds()


def _clock(seconds):
    seconds = max(0, int(seconds))
    return f"{seconds // 60}:{seconds % 60:02d}"


def _applications(count):
    return f"{count} application{'' if count == 1 else 's'}"


def _names(rows, limit=3):
    names = list(dict.fromkeys((" ".join((row["company"] or "").split()) or "an employer") for row in rows))
    return ", ".join(names[:limit]) + (f" and {len(names) - limit} more" if len(names) > limit else "")


def waiting_counts(session):
    """What a stop holds besides the queue: retries due, emails and answers that would restart an application, clicks
    remembered. A count of the owner's work, not of runs: `waiting` is what continues after Resume."""
    from .db import Outbox

    remembered = session.scalar(
        select(func.count()).select_from(Setting).where(Setting.key.startswith("owner_apply:", autoescape=True))
    ) or 0
    retries = sum(
        bool((value or {}).get("scheduled"))
        for value in session.scalars(
            select(Setting.value).where(Setting.key.startswith("draft_recovery:", autoescape=True))
        )
    )
    deliveries = session.scalar(
        select(func.count())
        .select_from(Outbox)
        .where(Outbox.state == "pending", Outbox.kind.in_(["mail_resume", "fact_resume", "dispatch"]))
    ) or 0
    return remembered + retries + deliveries


def sentence(stopped_now, closing, finishing, queued, waiting, *, close_left=0, finish_left=0):
    """The one sentence the owner reads: what is stopped, what is finishing, and when everything has."""
    if not stopped_now:
        return ""
    later = queued + waiting
    tail = (
        f"{_applications(later)} wait{'s' if later == 1 else ''} for Resume; none of them is lost."
        if later
        else "Press Resume when you want applying to continue."
    )
    parts = []
    if finishing:
        when = f"up to {_clock(finish_left)} more" if finish_left > 0 else "taking longer than usual, and it is not cut off"
        parts.append(f"{_applications(len(finishing))} in the final submit step will finish ({_names(finishing)}), {when}")
    if closing:
        when = f"about {_clock(close_left)} more" if close_left > 0 else "any moment now"
        parts.append(f"{_applications(len(closing))} closing, {when}")
    if parts:
        return "Stopping. Nothing new starts. " + "; ".join(parts) + ". " + tail
    return "Stopped. Nothing is applying and nothing new starts. " + tail


def status(db, at=None):
    """What the Stop control shows: read each second while something is still ending, so the countdown is the server's."""
    at = at or datetime.now(UTC)
    control = db.get_setting("control", {}) or {}
    stopped_now = bool(control.get("paused"))
    with db.session() as s:
        rows = [
            {"run_id": r.id, "job_id": r.job_id, "state": r.state, "company": r.company, "title": r.title}
            for r in s.execute(
                select(Run.id, Run.job_id, Run.state, Job.company, Job.title)
                .join(Job, Job.id == Run.job_id)
                .where(Run.state.in_(["queued", "running", "submitting"]))
            )
        ]
        queued = sum(r["state"] == "queued" for r in rows)
        running = [r for r in rows if r["state"] == "running"]
        submitting = [r for r in rows if r["state"] == "submitting"]
        waiting = waiting_counts(s) if stopped_now else 0
        began = dict(
            s.execute(
                select(Event.run_id, func.min(Event.created_at))
                .where(Event.run_id.in_([r["run_id"] for r in submitting]), Event.kind == "submitting")
                .group_by(Event.run_id)
            ).all()
        ) if submitting else {}
    since = _seconds_since(control.get("stopped_at"), at) if stopped_now else 0.0
    close_left = max(0, CLOSE_SECONDS - since)
    finish_left = 0.0
    for row in submitting:
        # From the moment its commit began, or the press if a continuation logged none. Whichever ends last is the number.
        left = FINISH_SECONDS - _seconds_since(began.get(row["run_id"], control.get("stopped_at") or now()), at)
        row["seconds_left"] = max(0, round(left))
        finish_left = max(finish_left, left)
    return {
        "stopped": stopped_now,
        "stopped_at": control.get("stopped_at") if stopped_now else None,
        # Everything has stopped: no browser is doing anything for an application.
        "settled": stopped_now and not running and not submitting,
        "running": len(running),
        "submitting": len(submitting),
        "queued": queued,
        "waiting": waiting,
        "closing": [{k: r[k] for k in ("run_id", "company", "title")} for r in running] if stopped_now else [],
        "finishing": [{k: r[k] for k in ("run_id", "company", "title", "seconds_left")} for r in submitting]
        if stopped_now
        else [],
        "seconds_left": round(max(close_left if running else 0, finish_left if submitting else 0)),
        "sentence": sentence(
            stopped_now, running, submitting, queued, waiting, close_left=close_left, finish_left=finish_left
        ),
    }
