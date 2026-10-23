"""Jobs: import, inspect, rank and start, pause, resume or remove an application."""

import asyncio

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy import select

from ...admission import QueueBlocked
from ...db import Job, Run, record
from ...discovery import add_job, rank_in_thread
from ...network import public_url
from ...schemas import JobInput, QueueInput


def router(service) -> APIRouter:
    db, config = service.db, service.config
    r = APIRouter()

    @r.post("/api/jobs")
    async def import_job(data: JobInput):
        await public_url(data.url, config.allow_private_urls)
        # The insert, the profile read and ranking stay off the event loop the browser owners share.
        row, created = await asyncio.to_thread(add_job, db, data, origin="manual")
        result = {"job": row, "created": created}
        profile = await asyncio.to_thread(db.get_setting, "profile", {})
        if created and profile.get("autonomous") and db.get_setting("control", {}).get("auto_submit"):
            try:
                await asyncio.to_thread(rank_in_thread, db, config, row["id"])
                with db.session() as s:
                    result["job"] = record(s.get(Job, row["id"]))
                if result["job"]["status"] == "ready":
                    result["run"] = await service.queue(row["id"], "submit")
            except QueueBlocked as exc:
                result["notice"] = "Job saved. " + str(exc)
                result["preparation"] = exc.as_dict()
            except Exception:
                # Import already committed. A downstream employer/provider outage
                # must not turn a saved job into a failed import or expose a URL,
                # credential or provider response through its exception text.
                with db.session() as s:
                    result["job"] = record(s.get(Job, row["id"]))
                result["notice"] = (
                    "Job saved. Automatic classification or queueing could not finish. You can retry from this job."
                )
        return result

    @r.get("/api/jobs/{job_id}")
    def get_job(job_id: str):
        with db.session() as s:
            row = s.execute(
                select(*(column for column in Job.__table__.columns if column.name != "description")).where(
                    Job.id == job_id
                )
            ).first()
            if row is None:
                raise HTTPException(status_code=404, detail="Job not found")
            latest = s.execute(
                select(
                    *(column for column in Run.__table__.columns if column.name not in {"packet", "receipt"})
                )
                .where(Run.job_id == job_id)
                .order_by(Run.created_at.desc(), Run.id.desc())
                .limit(1)
            ).first()
        control = db.get_setting("job_control:" + job_id, {})
        return {
            "job": dict(row._mapping),
            "latest_run": dict(latest._mapping) if latest else None,
            "job_control": {
                key: control[key] for key in ("state", "at", "previous_status") if key in control
            },
        }

    @r.post("/api/jobs/{job_id}/rank")
    async def classify(job_id: str):
        return await asyncio.to_thread(rank_in_thread, db, config, job_id)

    @r.post("/api/jobs/{job_id}/apply")
    async def apply_job(job_id: str):
        """One click: approve (no reason needed) and start; a deferred start is remembered, not an error."""
        if not service.dispatch:
            raise ValueError("Workers are disabled")
        from ...owner_apply import apply

        return await apply(service, job_id)

    @r.post("/api/jobs/{job_id}/queue")
    async def queue(job_id: str, data: QueueInput):
        if not service.dispatch:
            raise ValueError("Workers are disabled")
        try:
            result = await service.queue(
                job_id,
                data.mode,
                data.resume_id,
                controller_policy=data.controller_policy,
                resume_artifact_id=data.resume_artifact_id,
                owner_pick=data.mode == "submit",
            )
        except QueueBlocked as exc:
            if exc.code == "tailoring_pending":
                return JSONResponse(
                    status_code=202, content={"preparation": exc.as_dict(), "message": str(exc)}
                )
            if exc.outcome == "deferred" and data.mode == "submit":
                # Busy workers, pacing or a shared sender: the owner's request waits, it does not fail.
                from ...owner_apply import start

                return JSONResponse(status_code=202, content=await start(service, job_id))
            raise
        return {k: v for k, v in result.items() if k != "packet"}

    @r.post("/api/jobs/{job_id}/pause")
    async def pause_job(job_id: str):
        from ...job_control import manage

        return await manage(service, job_id, "pause")

    @r.post("/api/jobs/{job_id}/resume")
    async def resume_job(job_id: str, data: QueueInput | None = None):
        from ...job_control import manage

        try:
            return await manage(
                service, job_id, "resume", controller_policy=data.controller_policy if data else None
            )
        except QueueBlocked as exc:
            if exc.outcome != "deferred":
                raise
            from ...owner_apply import start

            return JSONResponse(status_code=202, content=await start(service, job_id))

    @r.delete("/api/jobs/{job_id}")
    async def remove_job(job_id: str):
        from ...job_control import manage

        return await manage(service, job_id, "delete")

    return r
