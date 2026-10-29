"""Jobs: import, inspect, rank and start, pause, resume or remove an application."""

import asyncio
from typing import Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from ...admission import QueueBlocked
from ...db import Job, Run, record
from ...discovery import add_job, rank_in_thread
from ...network import public_url
from ...owner_apply import MANY
from ...schemas import JobInput, QueueInput
from .. import pipeline

Group = Literal["all", "active", "needs", "repair", "unconfirmed", "paused", "done", "stopped", "new", "reserved"]


class ApplyMany(BaseModel):
    job_ids: list[str] = Field(min_length=1, max_length=MANY)


def router(service) -> APIRouter:
    db, config = service.db, service.config
    r = APIRouter()

    @r.get("/api/jobs")
    def pipeline_page(group: Group = "all", q: str = "", page: int = 1, size: int = 25):
        """One page of Jobs › Pipeline (pipeline.py); the group counts come with the snapshot."""
        with db.session() as s:
            return pipeline.page(s, group, q[:200], page, size)

    @r.post("/api/jobs/apply-many")
    async def apply_many(data: ApplyMany):
        """The owner's Apply for up to 50 jobs at once, each through the one-click path; a result per job."""
        if not service.dispatch:
            raise ValueError("Workers are disabled")
        from ...owner_apply import apply_many

        return await apply_many(service, data.job_ids)

    def saved_job(job_id):
        with db.session() as s:
            return record(s.get(Job, job_id))

    @r.post("/api/jobs")
    async def import_job(data: JobInput):
        await public_url(data.url, config.allow_private_urls)
        # The insert, the profile read and ranking stay off the event loop the browser owners share.
        row, created = await asyncio.to_thread(add_job, db, data, origin="manual")
        result = {"job": row, "created": created}
        profile = await asyncio.to_thread(db.get_setting, "profile", {})
        control = await asyncio.to_thread(db.get_setting, "control", {})
        if created and profile.get("autonomous") and control.get("auto_submit"):
            try:
                await asyncio.to_thread(rank_in_thread, db, config, row["id"])
                result["job"] = await asyncio.to_thread(saved_job, row["id"])
                if result["job"]["status"] == "ready":
                    result["run"] = await service.queue(row["id"], "submit")
            except QueueBlocked as exc:
                result["notice"] = "Job saved. " + str(exc)
                result["preparation"] = exc.as_dict()
            except Exception:
                # Import already committed. A downstream employer/provider outage
                # must not turn a saved job into a failed import or expose a URL,
                # credential or provider response through its exception text.
                result["job"] = await asyncio.to_thread(saved_job, row["id"])
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
            from ...reserved import approved, match

            # A reserved employer (FAANG, MANGO, the Big Four): the panel offers the owner's approval (2026-10-27).
            found = match(s, row)
            reserved = {**found.as_dict(), "approved": approved(s, job_id)} if found else None
        control = db.get_setting("job_control:" + job_id, {})
        # The owner's Apply waiting to start (owner_apply.start), so the job panel says so instead of "Eligible".
        request = db.get_setting("owner_apply:" + job_id)
        return {
            "job": dict(row._mapping),
            "latest_run": dict(latest._mapping) if latest else None,
            "job_control": {
                key: control[key] for key in ("state", "at", "previous_status") if key in control
            },
            "request": {k: request[k] for k in ("at", "code", "reason", "not_before") if k in request}
            if request
            else None,
            "reserved": reserved,
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
