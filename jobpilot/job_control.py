"""Owner controls for one job; submission evidence and identity are never deleted."""

import asyncio
from sqlalchemy import select

from .db import Job, Run, Setting, now
from .admission import QueueBlocked


def state(db, job_id):
    return db.get_setting("job_control:" + job_id, {})


def require_running(session, job_id):
    control = session.get(Setting, "job_control:" + job_id)
    if control and control.value.get("state") in {"paused", "deleted"}:
        raise QueueBlocked("This job is " + control.value["state"], outcome="protected", code="job_control")


async def manage(service, job_id, action, *, controller_policy=None, reopen_closed=False, actor="owner"):
    if controller_policy is not None:
        from .controller_policy import validate_policy

        validate_policy(controller_policy)
    if action not in {"pause", "resume", "delete"}:
        raise ValueError("Choose pause, resume or delete")
    with service.db.exclusive() as s:
        job = s.get(Job, job_id)
        if not job:
            raise ValueError("Job not found")
        runs = list(s.scalars(select(Run).where(Run.job_id == job_id).order_by(Run.created_at.desc())))
        last = runs[0] if runs else None
        active = next((r for r in runs if r.state in {"queued", "running"}), None)
        from .admission import unknown_resubmit_allowed

        committed = any(r.state in {"confirmed", "already_applied", "submitting"} for r in runs) or (
            any(r.state == "submission_unknown" for r in runs) and not unknown_resubmit_allowed(s, runs)
        )
        key = "job_control:" + job_id
        control = s.get(Setting, key)
        if (
            action == "resume"
            and any(r.state == "running" for r in runs)
            and control
            and control.value.get("state") == "paused"
        ):
            raise ValueError("Pause is saving the draft; resume when its state is Paused")
        if action == "resume" and control and control.value.get("state") == "deleted":
            raise ValueError("This job was removed; its application history is preserved")
        if action == "resume" and job.status == "closed" and not reopen_closed:
            # The employer's posting was found missing twice; retrying it wastes runs and owner attention.
            raise ValueError(
                "This posting is closed and was not retried. Reopen it deliberately to apply again"
            )
        if action == "resume" and committed:
            raise ValueError("Submission has started or has evidence; this job cannot be reapplied")
        previous_status = (control.value.get("previous_status") if control else None) or job.status
        value = {
            "state": {"pause": "paused", "delete": "deleted", "resume": "active"}[action],
            "at": now(),
            "previous_status": previous_status,
        }
        if control:
            control.value = value
        else:
            s.add(Setting(key=key, value=value))
        recovery = s.get(Setting, "draft_recovery:" + job_id)
        if recovery and action != "resume":
            recovery.value = {**recovery.value, "scheduled": False, "status": "owner_" + action}
        if action == "delete":
            job.status = "archived"
        if action in {"pause", "delete"}:
            for run in runs:
                if run.state == "queued":
                    run.state, run.finished_at = ("paused" if action == "pause" else "cancelled"), now()
            if action == "pause" and not committed and not any(r.state == "running" for r in runs):
                job.status = "paused"
        if action == "resume":
            if active:
                return {
                    "job_id": job_id,
                    "state": active.state,
                    "message": "This application is already active",
                }
            if last and last.packet.get("match_approved"):
                job.status = "ready"
            elif not last and job.status == "paused":
                job.status = previous_status
        mode = last.mode if last else "submit"
        resume_id = last.resume_id if last else job.resume_id
        rid = last.id if last else ""
        ready = job.status == "ready" or bool(last and last.mode == "dry_run" and resume_id)
    if actor == "owner":
        service.db.event(rid, "job_control", "Owner requested " + action, {"job_id": job_id, "action": action})
    if action == "resume":
        if not ready:
            return {
                "job_id": job_id,
                "state": "new",
                "message": "Resumed discovery; classify this job before applying",
            }
        run = await service.queue(job_id, mode, resume_id, retry=True, controller_policy=controller_policy)
        if actor != "owner":
            # Automatic retries were logged as "Owner requested resume" on every attempt, also when the queue refused
            # them (3,230 events in one hour on 2026-10-17). Only a retry that queued is recorded, by its real actor.
            service.db.event(rid, "job_control", f"Automatic retry queued ({actor})",
                             {"job_id": job_id, "action": action, "actor": actor})
        return {
            "job_id": job_id,
            "run_id": run["id"],
            "state": "queued",
            "message": "Preview resumed from its saved draft; it will not submit."
            if mode == "dry_run"
            else "Application resumed from its saved draft",
        }
    message = (
        "Job removed from the queue and job board. Application history is preserved."
        if action == "delete"
        else "This job is paused. Other applications keep running."
    )
    if committed:
        message += " A submission already sent will finish confirmation; this does not withdraw it."
    return {"job_id": job_id, "state": value["state"], "message": message}


def _run_and_control(db, run_id):
    """Synchronous DB read; watch() runs it in a thread so the shared event loop never blocks on it."""
    with db.session() as s:
        run = s.get(Run, run_id)
        if not run:
            return None, {}
        return run.state, state(db, run.job_id)


async def watch(service, engine, execution, run_id):
    """Release only this owner's slot, after its cleanup; never interrupt a commit."""
    while not execution.done():
        run_state, control = await asyncio.to_thread(_run_and_control, service.db, run_id)
        interrupt = (
            run_state == "running"
            and not getattr(engine, "armed", False)
            and control.get("state") in {"paused", "deleted"}
        )
        if interrupt:
            engine.result = {
                "state": "paused" if control["state"] == "paused" else "cancelled",
                "reason": "Owner " + control["state"] + " this application",
                "writer_released": True,
            }
            execution.cancel()
            return
        await asyncio.sleep(1.0)


def choices(service, reference=""):
    """Ambiguous employer names produce choices, never a guessed mutation."""
    from .answers import normalize

    ref = normalize(reference)
    with service.db.session() as s:
        jobs = list(
            s.scalars(
                select(Job)
                .where(Job.demo.is_(False), Job.status != "archived")
                .order_by(Job.created_at.desc())
            )
        )
        latest = {}
        for run in s.scalars(select(Run).order_by(Run.created_at.desc())):
            latest.setdefault(run.job_id, run)
        exact = [j for j in jobs if reference and (j.id == reference or j.url == reference)]
        matches = exact or [
            j
            for j in jobs
            if ref
            and (
                j.id.startswith(reference)
                and len(reference) >= 6
                or ref in normalize(j.company + " " + j.title)
            )
        ]
        if not reference:
            active = {"running", "submitting", "queued"}
            matches = sorted(
                jobs,
                key=lambda j: (
                    latest[j.id].state in active if j.id in latest else False,
                    latest[j.id].created_at if j.id in latest else "",
                    j.created_at,
                ),
                reverse=True,
            )[:10]
        result = []
        for j in matches[:10]:
            run = latest.get(j.id)
            control = state(service.db, j.id)
            result.append(
                {
                    "job_id": j.id,
                    "company": j.company,
                    "title": j.title,
                    "url": j.url,
                    "state": control.get("state")
                    if control.get("state") == "paused"
                    and (
                        not run
                        or run.state
                        not in {"confirmed", "submission_unknown", "already_applied", "submitting"}
                    )
                    else run.state
                    if run
                    else j.status,
                }
            )
        return result
