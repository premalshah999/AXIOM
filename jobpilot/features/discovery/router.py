"""/api/v2/discovery — readiness, bottleneck and policy (read-only views)."""

import threading
import time

from fastapi import APIRouter
from sqlalchemy import func, select

from .policy import DiscoveryPolicy
from .readiness import discover_due, ready_applications, status
from .tables import DcRequisition, DcSourceHealth


_TASKS: set = set()
STATUS_SECONDS = 20


def policy_for(db) -> DiscoveryPolicy:
    board = db.get_setting("job_board_settings", {}) or {}
    return DiscoveryPolicy(
        max_age_days=max(1, int(board.get("max_age_hours", 720)) // 24),
        early_career_only=board.get("early_career_only", False),
        minimum_fit=board.get("minimum_auto_fit", 70),
    )


def router(service) -> APIRouter:
    db = service.db
    owners = min(6, getattr(service.config, "workers", 8))
    r = APIRouter(prefix="/api/v2/discovery", tags=["discovery"])

    # The readiness overview reads every candidate posting twice; several open dashboards (and a fast reload) share
    # one answer for STATUS_SECONDS instead of each repeating it (2026-10-22).
    answered = {"at": -STATUS_SECONDS, "body": None}
    once = threading.Lock()

    @r.get("/status")
    def pipeline_status():
        with once:
            if answered["body"] is not None and time.monotonic() - answered["at"] < STATUS_SECONDS:
                return answered["body"]
            p = policy_for(db).model_copy(update={"owners": owners})
            answered.update(body=status(db, p), at=time.monotonic())
            return answered["body"]

    @r.get("/due")
    def due():
        p = policy_for(db).model_copy(update={"owners": owners})
        return discover_due(db, p).model_dump()

    @r.get("/ready")
    def ready(limit: int = 25):
        p = policy_for(db).model_copy(update={"owners": owners})
        return {"items": [x.model_dump() for x in ready_applications(db, p, limit=max(1, min(200, limit)))]}

    @r.get("/requisitions")
    def requisitions(page: int = 1, size: int = 25):
        size, page = max(1, min(100, size)), max(1, page)
        with db.session() as s:
            total = s.scalar(select(func.count()).select_from(DcRequisition))
            rows = s.scalars(
                select(DcRequisition)
                .order_by(DcRequisition.last_seen.desc())
                .offset((page - 1) * size)
                .limit(size)
            )
            items = [
                {
                    "id": x.id,
                    "job_id": x.job_id,
                    "title": x.title,
                    "location": x.location,
                    "posted_at": x.posted_at,
                    "posted_basis": x.posted_basis,
                    "first_seen": x.first_seen,
                    "sources": len(x.sources or []),
                }
                for x in rows
            ]
        return {"items": items, "total": total, "page": page, "size": size}

    @r.get("/sources/health")
    def health():
        with db.session() as s:
            rows = s.scalars(select(DcSourceHealth).where(DcSourceHealth.failures > 0).limit(200))
            return {
                "items": [
                    {
                        "source_id": x.source_id,
                        "failures": x.failures,
                        "next_attempt_at": x.next_attempt_at,
                        "last_error": x.last_error,
                    }
                    for x in rows
                ]
            }

    @r.get("/feeds")
    def feed_status():
        from .feeds import SWEEP_KEY

        return db.get_setting(SWEEP_KEY, {}) or {"state": "never"}

    @r.post("/feeds/refresh")
    async def feed_refresh():
        """Start an internet-wide new-grad sweep in the background."""
        import asyncio

        from .feeds import SWEEP_KEY, sweep

        state = db.get_setting(SWEEP_KEY, {}) or {}
        if state.get("state") == "running":
            return state
        task = asyncio.create_task(sweep(db, policy_for(db).max_age_days))
        _TASKS.add(task)
        task.add_done_callback(_TASKS.discard)
        return {"state": "running"}

    return r
