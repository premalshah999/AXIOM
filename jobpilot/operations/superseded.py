"""Close the email waits a newer run has replaced.

A run parked on an emailed code or link stays `waiting_email` after its matched email started a continuation, or after a
newer attempt started while the wait was open: 108 of 109 `waiting_email` runs belonged to jobs that had a newer run
(2026-10-29). Nothing ever closed them, so the status counts and the Telegram summary read "waiting for an employer
email" for applications that had long moved on, and their mail windows stayed in the sender's lease table.
"""

from sqlalchemy import select

from ..db import Event, MailChallenge, Run, now

ACTIVE = ("queued", "running", "submitting")
LIVE = ("pending", "matched", "consumed")


def close_superseded_waits(service, job_id=None, keep=()):
    """Cancel the `waiting_email` runs of `job_id` (every job when None) that a newer run replaced, and end their mail
    windows through the mail service. A wait is kept while a newer run is still active (it may be the continuation that
    owns the wait) and when its challenge is in `keep` (the newer run parked on that same window). Returns run ids."""
    closed, windows = [], []
    with service.db.exclusive() as s:
        query = select(Run.id, Run.job_id, Run.created_at, Run.packet["wait_handle"].as_json()).where(
            Run.state == "waiting_email"
        )
        if job_id:
            query = query.where(Run.job_id == job_id)
        for run_id, owner, created, handle in s.execute(query).all():
            newer = list(s.execute(select(Run.state).where(Run.job_id == owner, Run.created_at > created)).scalars())
            if not newer or any(state in ACTIVE for state in newer):
                continue
            handle = handle if isinstance(handle, dict) else {}
            challenge_ids = {handle.get("challenge_id") or handle.get("id")} - {None, ""}
            if challenge_ids & set(keep):
                continue
            run = s.get(Run, run_id)
            run.state, run.finished_at = "cancelled", now()
            run.reason = f"Replaced by a newer attempt; the email wait ended (was: {run.reason or 'waiting_email'})"[:4000]
            windows += [
                c.id
                for c in s.scalars(
                    select(MailChallenge).where(MailChallenge.run_id == run_id, MailChallenge.state.in_(LIVE))
                )
            ]
            s.add(Event(run_id=run_id, kind="email_wait_ended", message="A newer attempt replaced this email wait", data={}))
            closed.append(run_id)
    for challenge_id in windows:
        service.mail.outcome(challenge_id, "cancelled", "A newer attempt replaced this wait")
    return closed
