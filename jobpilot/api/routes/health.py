"""Liveness and runtime health."""

from fastapi import APIRouter
from sqlalchemy import func, select


def router(service) -> APIRouter:
    db, config = service.db, service.config
    r = APIRouter()

    @r.get("/healthz")
    def health():
        with db.session() as s:
            s.execute(select(1))
        return {"status": "ok", "version": "1.0.0"}

    @r.get("/api/runtime-health")
    def runtime_health():
        from ...runtime import engine_version
        from ...db import Outbox
        from ... import host_health
        from ...provider_health import selected
        from ...pipeline_comparison import snapshot as application_workers

        with db.session() as s:
            pending = s.scalar(
                select(func.count()).select_from(Outbox).where(Outbox.state.not_in(["delivered", "dead"]))
            )
            dead = s.scalar(select(func.count()).select_from(Outbox).where(Outbox.state == "dead"))
        return {
            "engine_version": engine_version(),
            "host": host_health.snapshot(db),
            "scheduler": db.get_setting("scheduler_health", {}),
            "heartbeat": db.get_setting("scheduler_heartbeat", {}),
            "outbox_pending": pending,
            "outbox_dead": dead,
            "workers": config.workers,
            "application_workers": application_workers(db, config.topology),
            "models": selected(db, config),
            "production_target": __import__(
                "jobpilot.autopilot", fromlist=["production_progress"]
            ).production_progress(db),
        }

    return r
