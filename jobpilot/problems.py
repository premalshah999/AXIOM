"""What went wrong recently: every attempt that ended without a sent application, newest first, with what the app does next.

A stop the app will retry reads "Applying" (vocabulary.py) and "Couldn't apply" lists only what it has given up on, so a failure with a
retry coming showed nowhere as a failure (owner, 2026-11-01: "why are the latest set of failures not visible"). This is the one list:
one row per attempt, in the words of `vocabulary.plain_sentence`, and "what next" from the retry code's own answers (outlook.given_up,
the scheduled recovery, the daily retry). Nothing here decides anything or starts anything.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from .db import Job, Review, Run, Setting
from .vocabulary import plain_sentence

# Attempts that ended without sending, or without proof. Running ones and sent ones are not problems; a replaced attempt has a newer row.
SHOWN = ("waiting_browser", "timed_out", "failed", "waiting_answer", "needs_review", "submission_unknown", "closed", "cancelled")
NOT_A_PROBLEM = ("Replaced by a newer attempt%", "Owner deleted%")
ALIVE = ("queued", "running", "submitting", "waiting_email")
KINDS = {"retry": "Will retry", "you": "Needs you", "stopped": "Stopped trying", "done": "Nothing more"}


def _clock(at, now):
    """A time a person reads: "3:40 PM" today, "Nov 2, 3:40 PM" after that. In the Mac's own time zone."""
    local = at.astimezone()
    today = now.astimezone().date()
    hour = local.strftime("%I:%M %p").lstrip("0")
    return hour if local.date() == today else f"{local.strftime('%b')} {local.day}, {hour}"


def _when(value):
    try:
        at = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return at if at.tzinfo else at.replace(tzinfo=UTC)


def _next_try(session, job_id, run, scheduled, runs, now):
    """(sentence, iso time or ""): when the retry that is coming is due. The same waits as recovery.retry_due and autopilot.stalled_candidates."""
    from .autopilot import MAINTENANCE, persist_wait
    from .recovery import backoff_seconds, nothing_sent_streak

    due = None
    if scheduled.get("scheduled") and scheduled.get("source_run_id") == run.id:
        due = _when(scheduled.get("not_before"))
    if due is None:
        ended = _when(run.finished_at or run.created_at) or now
        day = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        today = sum(1 for r in runs if r.created_at >= day)
        due = ended + persist_wait(today, run.reason)
        if streak := nothing_sent_streak((r.state, r.reason) for r in runs):
            due = max(due, ended + timedelta(seconds=backoff_seconds(streak, 60, 1800)))
        if MAINTENANCE.search(run.reason or ""):
            due = max(due, ended + timedelta(hours=1))
    if due <= now:
        return "Tries again as soon as a worker is free.", ""
    return f"Tries again around {_clock(due, now)}, or later if every worker is busy.", due.isoformat()


def _stopped_why(session, job_id):
    """Why no retry is coming, in the budget's own words when it is the budget."""
    from .admission import QueueBlocked
    from .application_budget import require_allowance

    try:
        require_allowance(session, job_id)
    except QueueBlocked as exc:
        return str(exc).rstrip(".") + "."
    return "The same problem came back with no change, so another try would end the same way."


def _what_next(session, row, job_runs, word, scheduled, unanswered, paused, now):
    """{kind, sentence, at} for one attempt. kind: retry, you, stopped, done."""
    newest = job_runs[-1]
    if any(r.state == "confirmed" and r.created_at > row.created_at for r in job_runs) or word == "applied":
        return {"kind": "done", "sentence": "Nothing. A later attempt was sent.", "at": ""}
    if newest.id != row.id:
        if newest.state in ALIVE:
            return {"kind": "retry", "sentence": "It is trying again now.", "at": ""}
        later = _when(newest.created_at)
        return {"kind": "done", "sentence": f"Nothing for this attempt. It tried again at {_clock(later, now)}; see the newer row.", "at": ""}
    if word == "needs" or row.id in unanswered:
        if row.state == "submission_unknown":
            return {"kind": "you", "sentence": "Check your email, or confirm it yourself. The app won't send it twice.", "at": ""}
        return {"kind": "you", "sentence": "Waiting for you: answer it under Needs you and the application continues.", "at": ""}
    if word == "applying":
        if paused:
            return {"kind": "retry", "sentence": "Tries again after you press Resume.", "at": ""}
        sentence, at = _next_try(session, row.job_id, newest, scheduled, job_runs, now)
        return {"kind": "retry", "sentence": sentence, "at": at}
    if word == "failed":
        return {"kind": "stopped", "sentence": "Nothing. The app has stopped trying: " + _stopped_why(session, row.job_id), "at": ""}
    return {"kind": "done", "sentence": "Nothing. There is no application left to send.", "at": ""}


def recent(session, days=7, page=1, size=50, kind="", now=None):
    """One page of the list. `kind` narrows it (retry, you, stopped, done) after what-next is known, so it reads the window and pages itself:
    a week is hundreds of attempts, read by the created_at index, and what-next reads only the jobs on the page unless a kind is asked for."""
    from .api.pipeline import words_of

    now = now or datetime.now(UTC)
    since = (now - timedelta(days=max(1, min(int(days), 30)))).isoformat()
    where = (
        Run.created_at >= since,
        Run.mode == "submit",
        Run.state.in_(SHOWN),
        Job.demo.is_(False),
        *[~Run.reason.like(pattern) for pattern in NOT_A_PROBLEM],
    )
    ended = func.coalesce(Run.finished_at, Run.created_at)
    columns = (Run.id, Run.job_id, Run.state, Run.reason, Run.created_at, Run.finished_at, Job.title, Job.company, Job.url, Job.ats, Job.location)
    picked = session.execute(select(*columns).join(Job, Job.id == Run.job_id).where(*where).order_by(ended.desc(), Run.id)).all()
    size = max(1, min(int(size), 200))
    rows = _describe(session, picked if kind else picked[max(0, (int(page) - 1) * size) : int(page) * size], now, words_of)
    counts = {}
    if kind:
        rows = [r for r in rows if r["next"]["kind"] == kind]
        counts[kind] = len(rows)
    total = len(rows) if kind else len(picked)
    pages = max(1, -(-total // size))
    number = max(1, min(int(page), pages))
    if kind:
        rows = rows[(number - 1) * size : number * size]
    return {"rows": rows, "total": total, "page": number, "pages": pages, "size": size, "days": days}


def _describe(session, picked, now, words_of):
    if not picked:
        return []
    job_ids = list({row.job_id for row in picked})
    run_ids = [row.id for row in picked]
    history = {}
    for run in session.execute(
        select(Run.id, Run.job_id, Run.state, Run.reason, Run.created_at, Run.finished_at).where(Run.job_id.in_(job_ids)).order_by(Run.created_at)
    ):
        history.setdefault(run.job_id, []).append(run)
    words = words_of(session, job_ids)
    recoveries = {
        row.key.removeprefix("draft_recovery:"): row.value or {}
        for row in session.scalars(select(Setting).where(Setting.key.in_(["draft_recovery:" + i for i in job_ids])))
    }
    unanswered = set(session.scalars(select(Review.run_id).where(Review.run_id.in_(run_ids), Review.answer.is_(None))))
    control = session.get(Setting, "control")
    paused = bool((control.value or {}).get("paused")) if control else False
    out = []
    for row in picked:
        runs = history[row.job_id]
        out.append(
            {
                "run_id": row.id,
                "job_id": row.job_id,
                "company": row.company,
                "title": row.title,
                "url": row.url,
                "ats": row.ats,
                "location": row.location,
                "state": row.state,
                "at": row.finished_at or row.created_at,
                "what": plain_sentence(row.reason) or "It stopped without saying why.",
                "next": _what_next(session, row, runs, words.get(row.job_id, "not"), recoveries.get(row.job_id, {}), unanswered, paused, now),
                "attempt": next((i for i, r in enumerate(runs, 1) if r.id == row.id), 1),
                "attempts": len(runs),
            }
        )
    return out
