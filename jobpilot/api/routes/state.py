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
            # Columns only: whole runs decoded every packet on each dashboard poll and starved the event loop
            # the browsers share (2026-10-21).
            rows = s.execute(
                select(
                    Run.id, Run.receipt, Run.finished_at, Run.created_at,
                    Job.id.label("job_id"), Job.title, Job.company, Job.url, Job.ats,
                )
                .join(Job, Run.job_id == Job.id)
                .where(Run.state == "confirmed", Job.demo.is_(False))
                .order_by(Run.finished_at.desc())
            ).all()
            seen, results = set(), []
            for row in rows:
                receipt = row.receipt or {}
                if row.job_id in seen or receipt.get("evidence_reconciliation") == "pending":
                    continue
                seen.add(row.job_id)
                results.append(
                    {
                        "job_id": row.job_id,
                        "run_id": row.id,
                        "title": row.title,
                        "company": row.company,
                        "url": row.url,
                        "applied_at": row.finished_at or row.created_at,
                        "ats": row.ats,
                        "website_confirmed": bool(receipt.get("website_confirmed")),
                        "email_confirmed": trusted_email(receipt),
                        "existing_application": bool(receipt.get("existing_application_notice")),
                    }
                )
            return {"applications": results}

    return r
