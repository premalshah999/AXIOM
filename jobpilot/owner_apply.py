"""One click applies (owner, 2026-10-18: "I do not want to explain why a job is a match, a click should do the work";
"queuing is super slow and bugging").

The owner's Apply approves the job (no reason to type; the job's resume, else the first uploaded one) and queues it.
When admission defers the start (all workers busy, a platform's pacing, a shared sender in use, an employer's
cooldown), that is not an error: the request is remembered, and the autopilot starts it as soon as admission allows.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from .admission import QueueBlocked
from .db import Job, Resume, Setting, now

KEY = "owner_apply:"
OWNER_APPROVED = "Owner approved: "
PREPARING = "Preparing a tailored resume first. The application starts on its own when it's ready."


def _later(not_before):
    return not_before or (datetime.now(UTC) + timedelta(seconds=60)).isoformat()


async def apply(service, job_id):
    """Approve and start one job, or remember it until admission allows."""
    with service.db.exclusive() as s:
        job = s.get(Job, job_id)
        if job is None:
            raise ValueError("Job not found")
        if job.status in {"queued", "running", "submitting"}:
            return {"job_id": job_id, "state": job.status, "message": "This application is already running."}
        if job.status in {"confirmed", "submission_unknown", "already_applied"}:
            return {"job_id": job_id, "state": job.status, "message": "This job is already applied."}
        if job.status == "archived":
            raise ValueError("This job was removed; restore it before applying")
        if job.status != "ready":
            job.resume_id = job.resume_id or s.scalar(select(Resume.id).where(Resume.demo.is_(False)).limit(1))
            if not job.resume_id:
                raise ValueError("Upload a resume before applying")
            job.status = "ready"
            if not (job.reason or "").startswith(OWNER_APPROVED):
                job.reason = OWNER_APPROVED + (job.reason or "")
        from .application_budget import MAX_STARTS, allowance

        if allowance(s, job_id)[2] >= MAX_STARTS:
            # The owner's click is the approval a fresh try needs (owner, 2026-10-18: "give them fresh tries").
            # Jobs that may already be submitted returned above, so this never repeats a submission.
            key = "application_budget_reset:" + job_id
            value = {"at": now(), "reason": "Owner clicked Apply"}
            if row := s.get(Setting, key):
                row.value = value
            else:
                s.add(Setting(key=key, value=value))
    return await start(service, job_id)


async def start(service, job_id, mode="submit", **queue_options):
    """Queue now; a deferred admission is remembered instead of reported as a failure."""
    try:
        run = await service.queue(job_id, mode, **queue_options)
    except QueueBlocked as exc:
        if exc.code == "tailoring_pending":
            return {"job_id": job_id, "state": "preparing", "preparing": True, "message": PREPARING}
        if exc.outcome != "deferred" or mode != "submit":
            raise
        service.db.set_setting(
            KEY + job_id,
            {"at": now(), "code": exc.code, "reason": str(exc)[:300], "not_before": _later(exc.not_before)},
        )
        return {
            "job_id": job_id,
            "state": "waiting",
            "deferred": True,
            "message": f"{str(exc).rstrip('.')}. It starts on its own as soon as it can.",
        }
    _forget(service.db, job_id)
    return {"job_id": job_id, "run_id": run["id"], "state": "queued", "message": "Application queued."}


def _forget(db, job_id):
    with db.exclusive() as s:
        row = s.get(Setting, KEY + job_id)
        if row is not None:
            s.delete(row)


async def due(service, limit):
    """Start remembered owner requests whose wait is over, oldest first. Returns how many were queued."""
    if limit <= 0:
        return 0
    with service.db.session() as s:
        rows = sorted(
            ((row.key.removeprefix(KEY), dict(row.value or {})) for row in s.scalars(
                select(Setting).where(Setting.key.like(KEY + "%"))
            )),
            key=lambda item: item[1].get("at", ""),
        )
    started = 0
    for job_id, value in rows:
        if started >= limit:
            break
        if (value.get("not_before") or "") > now():
            continue
        try:
            await service.queue(job_id, "submit")
        except QueueBlocked as exc:
            if exc.outcome == "deferred":
                service.db.set_setting(KEY + job_id, {**value, "code": exc.code, "reason": str(exc)[:300],
                                                      "not_before": _later(exc.not_before)})
                continue
            _forget(service.db, job_id)  # A permanent refusal (closed, protected, excluded) ends the request.
            continue
        except ValueError:
            _forget(service.db, job_id)
            continue
        _forget(service.db, job_id)
        started += 1
    return started
