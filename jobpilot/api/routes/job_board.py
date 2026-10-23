"""Authenticated job board routes; manual application clicks use normal queue protections."""

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from ...db import Job, Run, Source, now
from ...discovery import rank_batch
from ...jobboard import BoardSettings, board_snapshot, managed_ids, setup_sources, settings
from ...network import public_url
from ...schemas import SourceInput
from ...service import ACTIVE


class ApplySelection(BaseModel):
    job_ids: list[str] = Field(min_length=1, max_length=50)


class BatchStart(BaseModel):
    target: int = Field(default=100, ge=1, le=500)


def router(service) -> APIRouter:
    db, config = service.db, service.config
    r = APIRouter()

    @r.post("/api/job-board/autopilot/start")
    async def start_autopilot(data: BatchStart):
        from ...autopilot import start_autonomous, batch_snapshot

        if not service.dispatch:
            raise HTTPException(409, "Application workers are disabled")
        return {**await start_autonomous(service, data.target), "runtime": batch_snapshot(service)}

    @r.get("/api/job-board")
    def snapshot(include_sources: bool = True):
        board = board_snapshot(db)
        board["source_count"] = len(board["sources"])
        if not include_sources:
            board["sources"] = []
        return {
            **board,
            "search": {
                "google_configured": bool(config.serper_api_key),
                "provider": "Google / Serper"
                if config.serper_api_key
                else "Free web search (DuckDuckGo / Bing)",
            },
        }

    @r.put("/api/job-board/settings")
    async def update(data: BoardSettings):
        if data.auto_apply:
            from ...autopilot import start_batch

            start_batch(db, data.batch_target)
        count = setup_sources(db, data)
        return {"saved": True, "sources": count}

    @r.post("/api/job-board/sources")
    async def add_source(data: SourceInput):
        await public_url(data.url, config.allow_private_urls)
        ids = managed_ids(db)
        cfg = settings(db)
        with db.exclusive() as s:
            row = s.scalar(select(Source).where(Source.url == data.url))
            if row and row.id not in ids:
                raise HTTPException(
                    409,
                    "This board is already configured in Sources. Remove that entry before adding it here.",
                )
            if not row:
                row = Source(
                    **{
                        **data.model_dump(),
                        "auto_queue": False,
                        "enabled": data.enabled and cfg.enabled,
                        "cron": f"0 */{cfg.interval_hours} * * *",
                        "next_run": (datetime.now(UTC) + timedelta(hours=cfg.interval_hours)).isoformat(),
                    }
                )
                s.add(row)
                s.flush()
            ids.add(row.id)
        db.set_setting("job_board_sources", {"ids": sorted(ids)})
        return {"added": True, "id": row.id}

    @r.post("/api/job-board/scrape")
    async def scrape_now():
        if not service.dispatch:
            raise HTTPException(409, "Start application workers before scraping")
        if not settings(db).enabled:
            raise HTTPException(409, "Enable job discovery first")
        setup_sources(db)
        ids = managed_ids(db)
        with db.session() as s:
            ids = list(s.scalars(select(Source.id).where(Source.id.in_(ids), Source.enabled.is_(True))))
        from ...workers import enqueue_source

        started = now()
        queued = 0
        for sid in ids:
            scan = db.get_setting("job_board_scan:" + sid, {})
            if scan.get("state") in {"queued", "running"} and datetime.now(UTC) - datetime.fromisoformat(
                scan.get("started_at", started)
            ) < timedelta(hours=6):
                continue
            db.set_setting("job_board_scan:" + sid, {"state": "queued", "started_at": started})
            try:
                queued += bool(await enqueue_source(sid))
            except Exception:
                db.set_setting(
                    "job_board_scan:" + sid,
                    {"state": "error", "started_at": started, "error": "Could not queue this source"},
                )
        db.set_setting("job_board_requested", {"at": started})
        return {"queued_sources": queued, "total_sources": len(ids)}

    @r.post("/api/job-board/apply")
    async def apply_selection(data: ApplySelection):
        if not service.dispatch:
            raise HTTPException(409, "Application workers are disabled")
        control = db.get_setting("control", {})
        if not control.get("auto_submit") or control.get("paused"):
            raise HTTPException(409, "Enable automatic submission and resume workers in Settings first")
        fresh_ids = {j["id"] for j in board_snapshot(db)["jobs"]}
        queued, skipped = [], []
        with db.session() as s:
            attempted = set(
                s.scalars(
                    select(Run.job_id).where(
                        Run.state.in_(ACTIVE | {"confirmed", "submission_unknown", "already_applied"})
                    )
                )
            )
            unranked = [
                jid
                for jid in dict.fromkeys(data.job_ids)
                if jid in fresh_ids and jid not in attempted and s.get(Job, jid).status != "ready"
            ]
        for offset in range(0, len(unranked), 24):
            await rank_batch(db, config, unranked[offset : offset + 24])
        for jid in dict.fromkeys(data.job_ids):
            if jid not in fresh_ids:
                skipped.append({"job_id": jid, "reason": "No longer a fresh US listing"})
                continue
            with db.session() as s:
                blocked = s.scalar(
                    select(Run.id).where(
                        Run.job_id == jid,
                        Run.state.in_(ACTIVE | {"confirmed", "submission_unknown", "already_applied"}),
                    )
                )
            if blocked:
                skipped.append(
                    {"job_id": jid, "reason": "Already active, submitted or awaiting confirmation"}
                )
                continue
            try:
                # Recheck expiry after classification, which may take several seconds.
                if jid not in {j["id"] for j in board_snapshot(db)["jobs"]}:
                    raise ValueError("Posting aged out during classification")
                run = await service.queue(jid, "submit", owner_pick=True)  # The owner's own selection.
                queued.append({"job_id": jid, "run_id": run["id"]})
            except (ValueError, RuntimeError) as exc:
                skipped.append({"job_id": jid, "reason": str(exc)[:250]})
        return {"queued": queued, "skipped": skipped}

    return r
