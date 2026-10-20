"""Transactional, restart-safe delivery outside browser worker reservations."""

import asyncio
import hashlib
import time

from sqlalchemy import and_, case, exists, or_, select
from sqlalchemy.orm import aliased

from .db import Job, Outbox, Review, Run, Setting, now, record


# A delivery that keeps failing is parked as "dead" after this many attempts instead of being retried for ever. Retries
# back off to five minutes, so 12 attempts is about an hour and 288 about a day. Chat, report and repair deliveries are
# worth an hour; the rest may be waiting out a legitimate deferral (a spent daily model budget, an employer's pacing).
# Production history: every row was eventually delivered, the most retried needed 53 attempts (fact_resume) and 40
# (notify, before the per-run cap in telegram.record_notification_error).
MAX_ATTEMPTS = {"notify": 12, "report": 12, "refill": 12, "repair": 6}
DEFAULT_MAX_ATTEMPTS = 288


def enqueue(session, kind, key, payload=None, run_id=""):
    identity = hashlib.sha256((kind + ":" + key).encode()).hexdigest()
    row = session.get(Outbox, identity)
    if row is None:
        row = Outbox(id=identity, kind=kind, run_id=run_id, payload=payload or {})
        session.add(row)
    return row


def sweep_answered_drafts(service, limit=200):
    """Backfill a durable wakeup for latest drafts whose entire review set is answered."""
    later = aliased(Run)
    added = 0
    with service.db.exclusive() as session:
        query = (
            select(Run, Job)
            .join(Job, Job.id == Run.job_id)
            .where(
                Run.state.in_(["waiting_answer", "needs_review"]),
                Job.status.not_in(["archived", "confirmed", "submission_unknown", "paused"]),
                ~exists(select(Review.id).where(Review.run_id == Run.id, Review.answer.is_(None))),
                ~exists(
                    select(later.id).where(
                        later.job_id == Run.job_id,
                        or_(
                            later.created_at > Run.created_at,
                            and_(later.created_at == Run.created_at, later.id > Run.id),
                        ),
                    )
                ),
            )
            .order_by(Run.created_at.desc())
            .limit(limit)
        )
        for run, job in session.execute(query):
            control = session.get(Setting, "job_control:" + job.id)
            if control and control.value.get("state") in {"paused", "deleted"}:
                continue
            reviews = list(
                session.scalars(
                    select(Review)
                    .where(
                        Review.run_id == run.id,
                        Review.key.not_in(["manual", "session"]),
                        Review.answer.is_not(None),
                    )
                    .order_by(Review.created_at, Review.id)
                )
            )
            if not reviews or run.mode == "submit" and not run.packet.get("match_approved"):
                continue
            key = (
                "answered-set:"
                + run.id
                + ":"
                + hashlib.sha256(",".join(sorted(r.id for r in reviews)).encode()).hexdigest()[:16]
            )
            identity = hashlib.sha256(("fact_resume:" + key).encode()).hexdigest()
            if session.get(Outbox, identity) is None:
                enqueue(session, "fact_resume", key, {"review_id": reviews[-1].id}, run_id=run.id)
                added += 1
    return added


def claim(db, kinds=None):
    current = time.time()
    with db.exclusive() as s:
        query = (
            select(Outbox)
            .where(
                Outbox.available_at <= current,
                or_(
                    Outbox.state == "pending", (Outbox.state == "processing") & (Outbox.lease_until < current)
                ),
            )
            .order_by(
                case(
                    (Outbox.kind == "mail_resume", 0),
                    (Outbox.kind == "refill", 1),
                    (Outbox.kind == "notify", 2),
                    else_=3,
                ),
                Outbox.created_at,
            )
            .limit(1)
        )
        if kinds:
            query = query.where(Outbox.kind.in_(kinds))
        row = s.scalar(query)
        if row is None:
            return None
        row.state, row.lease_until = "processing", current + 120
        row.attempts += 1
        return record(row)


async def deliver(service, item):
    payload, kind = item["payload"], item["kind"]
    if kind == "report":
        from .failure_reports import create_report

        await create_report(service, item["run_id"], payload.get("diagnostics", {}))
    elif kind == "notify":
        from .telegram import notify_run
        from .telegram_workers import any_configured

        if any_configured(service.config):
            await notify_run(service, item["run_id"])
            pending = service.db.get_setting("telegram_run_pending:" + item["run_id"], {})
            if pending.get("pending"):
                raise RuntimeError("Notification is still pending")
    elif kind == "refill":
        if service.dispatch and service.config.enable_workers:
            from .workers import enqueue_matching

            await enqueue_matching()
    elif kind == "fact_resume":
        await service.resume_review(payload["review_id"])
    elif kind == "mail_resume":
        await service.resume_mail_wait(payload["run_id"], payload["challenge_id"])
    elif kind == "dispatch":
        if not service.dispatch:
            raise RuntimeError("Workers are not ready for delivery")
        await service.dispatch(payload["run_id"])
    elif kind == "repair":
        from .repair_agent import repair_run

        await repair_run(service, item["run_id"])
    elif kind == "tailor":
        if not service.dispatch or not service.config.enable_workers:
            raise RuntimeError("Workers are not ready for tailoring")
        from .workers import enqueue_tailoring

        await enqueue_tailoring(payload["job_id"])
    else:
        raise ValueError("Unknown outbox delivery kind")


def owns_delivery(row, item):
    """Only the callback holding this exact claim may finish or retry it."""
    return bool(
        row
        and row.state == "processing"
        and row.attempts == item["attempts"]
        and row.lease_until == item["lease_until"]
    )


async def process_one(service, kinds=None):
    item = claim(service.db, kinds)
    if item is None:
        return False
    try:
        await deliver(service, item)
    except asyncio.CancelledError:
        with service.db.exclusive() as s:
            row = s.get(Outbox, item["id"])
            if owns_delivery(row, item):
                row.state, row.lease_until = "pending", 0
        raise
    except Exception as exc:
        dead = None
        with service.db.exclusive() as s:
            row = s.get(Outbox, item["id"])
            if owns_delivery(row, item):
                row.error = type(exc).__name__  # Provider payloads and secrets never enter the outbox.
                if row.attempts >= MAX_ATTEMPTS.get(row.kind, DEFAULT_MAX_ATTEMPTS):
                    row.state, row.lease_until, row.finished_at = "dead", 0, now()
                    dead = (row.kind, row.run_id, row.attempts, row.error)
                else:
                    row.state, row.lease_until = "pending", 0
                    row.available_at = time.time() + min(300, 2 ** min(row.attempts, 8))
        if dead:
            park(service, *dead)
    else:
        with service.db.exclusive() as s:
            row = s.get(Outbox, item["id"])
            if owns_delivery(row, item):
                row.state, row.lease_until, row.error = "delivered", 0, ""
                row.finished_at = now()
    return True


def park(service, kind, run_id, attempts, error):
    """A delivery gave up for good: say so on its run, and stop the Telegram path from retrying around it."""
    if not run_id:
        return
    try:
        service.db.event(
            run_id, "outbox_dead", f"Delivery of {kind} gave up after {attempts} attempts ({error})", {"kind": kind}
        )
        if kind == "notify":
            service.db.set_setting("telegram_run_pending:" + run_id, {"pending": False})
    except Exception:
        pass  # Parking is bookkeeping; the row is already dead.


async def poll(service):
    async def worker(kinds):
        next_sweep = 0
        while True:
            try:
                if kinds and "fact_resume" in kinds:
                    from .execution_services import forward_fact_events

                    forward_fact_events(service)
                    if time.monotonic() >= next_sweep:
                        await asyncio.to_thread(sweep_answered_drafts, service)
                        next_sweep = time.monotonic() + 30
                busy = await process_one(service, kinds)
            except asyncio.CancelledError:
                raise
            except Exception:
                busy = False
            if not busy:
                await asyncio.sleep(1)

    await asyncio.gather(
        worker(["mail_resume", "fact_resume", "dispatch", "refill", "tailor"]),
        worker(["notify"]),
        worker(["report"]),
        worker(["repair"]),
    )
