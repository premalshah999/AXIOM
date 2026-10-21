"""Archived failed applications and their downloadable logs."""

import asyncio

from fastapi import APIRouter
from fastapi.responses import FileResponse


def router(service) -> APIRouter:
    db = service.db
    r = APIRouter()

    @r.get("/api/archive")
    def archived():
        from ...archive import summaries

        rows = summaries(db)
        return {"applications": rows, "log_bytes": sum(r.get("log_bytes", 0) for r in rows)}

    @r.post("/api/archive/failed")
    async def archive_failures():
        from ...archive import archive_failed

        return await asyncio.to_thread(archive_failed, service)

    @r.get("/api/archive/{job_id}/log")
    def archived_log(job_id: str):
        from ...archive import log_path

        path = log_path(service, job_id)
        return FileResponse(
            path, media_type="application/gzip", filename=f"applypilot-{job_id[:8]}-log.jsonl.gz"
        )

    return r
