"""Cancel saved drafts during owner-requested maintenance, preserving submission proof."""

from sqlalchemy import select

from ..db import Job, MailChallenge, Outbox, Run, Setting, now
from .reset import PROTECTED_STATES

DRAFT_STATES = {"queued", "paused", "waiting_answer", "waiting_browser", "waiting_email", "needs_review"}


def cancel_saved_drafts(db):
    """Run only after pausing dispatch and draining all browser owners.

    Keep packets, reviews, events and receipts for diagnosis. A submitted job's
    older unsent draft can be cancelled without changing its protected job state.
    """
    with db.exclusive() as session:
        control = session.get(Setting, "control")
        if not control or not control.value.get("paused") or control.value.get("auto_submit"):
            raise RuntimeError("Pause workers and disable submission before cancelling drafts")
        if session.scalar(select(Run.id).where(Run.state.in_({"running", "submitting"})).limit(1)):
            raise RuntimeError("Active applications must drain before cancelling saved drafts")
        runs = list(session.scalars(select(Run)))
        protected_jobs = {run.job_id for run in runs if run.state in PROTECTED_STATES or run.receipt}
        cancelled, jobs, preserved = set(), set(), 0
        for run in runs:
            if run.state not in DRAFT_STATES:
                continue
            jobs.add(run.job_id)
            desktop = session.get(Setting, "desktop_job:" + run.job_id)
            if (
                run.receipt
                or run.packet.get("verification_only")
                or run.packet.get("reservation_run_id")
                or desktop
                and desktop.value.get("armed")
            ):
                preserved += 1
                protected_jobs.add(run.job_id)
                continue
            run.state, run.finished_at = "cancelled", now()
            run.reason = "Owner cancelled saved application during maintenance"
            cancelled.add(run.id)
        for job_id in jobs:
            job = session.get(Job, job_id)
            if job and job_id not in protected_jobs and job.status not in PROTECTED_STATES | {"archived"}:
                job.status = "cancelled"
            key = "job_control:" + job_id
            row = session.get(Setting, key)
            if not row or row.value.get("state") != "deleted":
                value = {"state": "paused", "at": now(), "reason": "owner_cancelled_drafts"}
                if row:
                    row.value = {**row.value, **value}
                else:
                    session.add(Setting(key=key, value=value))
            recovery = session.get(Setting, "draft_recovery:" + job_id)
            if recovery:
                recovery.value = {**recovery.value, "scheduled": False, "status": "owner_cancelled"}
        deliveries = challenges = 0
        for row in session.scalars(select(Outbox).where(Outbox.state.in_(["pending", "processing"]))):
            if row.run_id in cancelled:
                row.state, row.finished_at, row.lease_until = "cancelled", now(), 0
                deliveries += 1
        for row in session.scalars(
            select(MailChallenge).where(MailChallenge.state.in_(["pending", "matched"]))
        ):
            if row.run_id in cancelled:
                row.state, row.payload, row.reason = "cancelled", "", "Owner cancelled application"
                challenges += 1
        result = {
            "cancelled_runs": len(cancelled),
            "jobs": len(jobs),
            "preserved_reserved_drafts": preserved,
            "cancelled_deliveries": deliveries,
            "cancelled_challenges": challenges,
        }
        marker = session.get(Setting, "maintenance:cancelled_drafts")
        value = {**result, "at": now()}
        if marker:
            marker.value = value
        else:
            session.add(Setting(key="maintenance:cancelled_drafts", value=value))
        return result
