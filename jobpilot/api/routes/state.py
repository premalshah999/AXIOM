"""Dashboard state: the one-poll snapshot and the ledger of confirmed applications."""

import time

from fastapi import APIRouter
from fastapi.responses import Response
from sqlalchemy import select

from ...db import Job, Run
from ..snapshot import SnapshotCache, build_snapshot, trusted_email


def router(service, cache: SnapshotCache) -> APIRouter:
    db, config = service.db, service.config
    r = APIRouter()

    @r.get("/api/snapshot")
    def snapshot(include_sources: bool = True):
        if include_sources or config.snapshot_cache_seconds <= 0:
            return build_snapshot(service, include_sources)
        body, age = cache.body, time.monotonic() - cache.at
        if body is not None and not cache.stale and age < 30:
            if age >= config.snapshot_cache_seconds:
                cache.refresh()
            return Response(body, media_type="application/json")
        return Response(cache.build(), media_type="application/json")

    @r.get("/api/successful-applications")
    def successes():
        with db.session() as s:
            rows = s.execute(
                select(Run, Job)
                .join(Job, Run.job_id == Job.id)
                .where(Run.state == "confirmed", Job.demo.is_(False))
                .order_by(Run.finished_at.desc())
            ).all()
            seen, results = set(), []
            for run, job in rows:
                if job.id in seen or run.receipt.get("evidence_reconciliation") == "pending":
                    continue
                seen.add(job.id)
                results.append(
                    {
                        "job_id": job.id,
                        "run_id": run.id,
                        "title": job.title,
                        "company": job.company,
                        "url": job.url,
                        "applied_at": run.finished_at or run.created_at,
                        "ats": job.ats,
                        "website_confirmed": bool(run.receipt.get("website_confirmed")),
                        "email_confirmed": trusted_email(run.receipt),
                        "existing_application": bool(run.receipt.get("existing_application_notice")),
                    }
                )
            return {"applications": results}

    return r
