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
from .service.queueing import off_loop

KEY = "owner_apply:"
# A job the owner applied to or pasted. Its retries keep the owner's say over the autopilot's fit filters.
PICK = "owner_pick:"
OWNER_APPROVED = "Owner approved: "
PREPARING = "Preparing a tailored resume first. The application starts on its own when it's ready."
# One request applies to at most this many jobs (Jobs › Discovered "Apply to selected", 2026-10-22).
MANY = 50


def picked(session, job_id):
    return session.get(Setting, PICK + job_id) is not None


def _later(not_before):
    return not_before or (datetime.now(UTC) + timedelta(seconds=60)).isoformat()


async def apply(service, job_id):
    """Approve and start one job, or remember it until admission allows."""
    answered = await off_loop(_approve, service, job_id)
    return answered or await start(service, job_id)


async def apply_many(service, job_ids):
    """The owner's Apply for several jobs (owner, 2026-10-22: "for unfit applications there should be an option of
    ticking it or select all, to apply to a few at once rather than opening every single one").

    Each job goes through exactly the single-click path, one after another, so every duplicate-submission and
    admission guard decides it as it would a click. The answer reports each job: queued, waiting (with the reason),
    already running or applied, or refused (with the reason).
    """
    results = []
    for job_id in list(dict.fromkeys(job_ids))[:MANY]:
        try:
            result = await apply(service, job_id)
            # queued | waiting | preparing; a job that was already queued, running or applied needed no start.
            started = result["state"] != "queued" or result.get("run_id")
            outcome = "already" if result["state"] in ALREADY or not started else result["state"]
        except QueueBlocked as exc:
            result = {"job_id": job_id, "state": "refused", "code": exc.code, "message": str(exc)[:300]}
            outcome = "refused"
        except ValueError as exc:
            result, outcome = {"job_id": job_id, "state": "refused", "message": str(exc)[:300]}, "refused"
        results.append({**result, "outcome": outcome})
    names = await off_loop(_names, service.db, [result["job_id"] for result in results])
    results = [{**names.get(result["job_id"], {}), **result} for result in results]
    summary = {}
    for result in results:
        summary[result["outcome"]] = summary.get(result["outcome"], 0) + 1
    return {"results": results, "summary": summary}


def _names(db, job_ids):
    """Company and title per job, so the summary names each job without another request."""
    with db.session() as s:
        rows = s.execute(select(Job.id, Job.company, Job.title).where(Job.id.in_(job_ids))).all()
    return {row.id: {"company": row.company, "title": row.title} for row in rows}


# _approve's answers for a job that needs no start.
ALREADY = {"running", "submitting", "confirmed", "submission_unknown", "already_applied"}


def _approve(service, job_id):
    """The click's approval, in one short transaction; an answer when the job needs no start."""
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
    return None


async def start(service, job_id, mode="submit", **queue_options):
    """Queue now; a deferred admission is remembered instead of reported as a failure."""
    try:
        run = await service.queue(job_id, mode, owner_pick=mode == "submit", **queue_options)
    except QueueBlocked as exc:
        if exc.code == "tailoring_pending":
            return {"job_id": job_id, "state": "preparing", "preparing": True, "message": PREPARING}
        if exc.outcome != "deferred" or mode != "submit":
            raise
        await off_loop(
            service.db.set_setting,
            KEY + job_id,
            {"at": now(), "code": exc.code, "reason": str(exc)[:300], "not_before": _later(exc.not_before)},
        )
        return {
            "job_id": job_id,
            "state": "waiting",
            "deferred": True,
            "code": exc.code,
            "message": f"{str(exc).rstrip('.')}. It starts on its own as soon as it can.",
        }
    await off_loop(_forget, service.db, job_id)
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
            await service.queue(job_id, "submit", owner_pick=True)
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
