"""Authenticated job board routes; manual application clicks use normal queue protections."""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from ...db import Job, Run, Source, now
from ...features.discovery.tables import DcSourceHealth
from ...discovery import rank_batch
from ...jobboard import LOCKED, BoardSettings, board_snapshot, fit_of, managed_ids, setup_sources, settings
from ...network import public_url
from ...schemas import SourceInput
from ...service import ACTIVE
from ..snapshot import BATCH_BOOKKEEPING


Fit = Literal["all", "eligible", "unranked", "below", "uncertain"]
SELECTABLE = 500


def unapplied(job):
    """Discovered lists jobs not applied to yet: no run, no waiting Apply, not owned, paused or protected. Those
    are in Jobs › Pipeline (owner, 2026-10-23: one clear split between the pipeline and discovery). A reserved
    employer's job waiting for the owner's approval is in Jobs › Reserved (2026-10-27)."""
    return (
        not job.get("attempted")
        and not job.get("requested")
        and not job.get("reserved")
        and job["status"] not in LOCKED
    )


# Consecutive failures after which a board is called dead: the back-off (1, 2, 4 days...) has started by the third.
DEAD_AFTER = 3


def source_health(db, sources):
    """Consecutive scan failures per source (dc_source_health), for Setup › Job board. Each source is annotated in
    place with `health`; the counts cover the sources given. 450 of 4,263 boards were dead (404/422) on 2026-10-29
    and only their error text said so."""
    with db.session() as s:
        rows = {
            row.source_id: {"failures": row.failures, "last_error": row.last_error, "last_ok": row.last_ok}
            for row in s.scalars(select(DcSourceHealth).where(DcSourceHealth.failures > 0))
        }
    for source in sources:
        source["health"] = rows.get(source["id"]) or {"failures": 0, "last_error": "", "last_ok": None}
    # A board the owner switched off is no longer a problem to show.
    failing = [s["health"] for s in sources if s["health"]["failures"] and s.get("enabled", True)]
    return {"failing": len(failing), "dead": sum(row["failures"] >= DEAD_AFTER for row in failing)}


def listing_page(jobs, minimum, q, ats, level, fit, page, size):
    """Jobs › Discovered, filtered and paged here: the dashboard received 500 listings (577 KB) every 20 s and
    filtered them itself, so a board with more fresh listings than that showed wrong totals and missed rows
    (2026-10-22). `fits` counts each fit filter under the other filters; `selectable_ids` lets "select all" cover
    every matching job, not only the page."""
    text = (q or "").strip().lower()
    jobs = [job for job in jobs if unapplied(job)]

    def keep(job):
        if text and text not in f"{job['title']} {job['company']} {job['location']}".lower():
            return False
        if ats != "all" and job["ats"] != ats:
            return False
        return level == "all" or job["career_level"] == level

    shown = [job for job in jobs if keep(job)]
    fits = {"all": len(shown), "eligible": 0, "unranked": 0, "below": 0, "uncertain": 0}
    for job in shown:
        fits[fit_of(job, minimum)] += 1
    matching = shown if fit == "all" else [job for job in shown if fit_of(job, minimum) == fit]
    size = max(1, min(100, size))
    pages = max(1, -(-len(matching) // size))
    page = max(1, min(page, pages))
    return {
        "jobs": matching[(page - 1) * size : page * size],
        "total": len(matching),
        "fresh_total": len(jobs),
        "fits": fits,
        "platforms": sorted({job["ats"] for job in jobs}),
        "page": page,
        "size": size,
        "pages": pages,
        "selectable_ids": [job["id"] for job in matching][:SELECTABLE],
        "selectable_total": len(matching),
    }


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
    def snapshot(
        include_sources: bool = True,
        page: int | None = None,
        size: int = 25,
        q: str = "",
        ats: str = "all",
        level: str = "all",
        fit: Fit = "all",
    ):
        """With `page`, one page of the fresh listings matching the filters and exact totals; without it, the newest
        500 as before."""
        board = board_snapshot(db, limit=None if page else 500)
        from ...reserved import held_ids

        held = held_ids(db)
        # Why the batch has not started a job (autopilot.remember_rejection): only for a job nothing has been done to
        # yet, so the Discovered list answers "why isn't this applying?" without opening the job (2026-10-29).
        rejections = db.get_setting("job_board_autopilot", {}).get("rejections") or {}
        for job in board["jobs"]:
            job["reserved"] = job["id"] in held
            if (why := rejections.get(job["id"])) and unapplied(job):
                job["rejection"] = why
        board["source_count"] = len(board["sources"])
        board["source_health"] = source_health(db, board["sources"])
        if not include_sources:
            board["sources"] = []
        board["autopilot"] = {k: v for k, v in board["autopilot"].items() if k not in BATCH_BOOKKEEPING}
        if page:
            board.update(
                listing_page(
                    board["jobs"], board["settings"]["minimum_auto_fit"], q, ats, level, fit, page, size
                )
            )
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
    def update(data: BoardSettings):
        if data.auto_apply:
            from ...autopilot import start_batch

            start_batch(db, data.batch_target)
        count = setup_sources(db, data)
        return {"saved": True, "sources": count}

    @r.post("/api/job-board/sources")
    async def add_source(data: SourceInput):
        await public_url(data.url, config.allow_private_urls)

        def save():
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
            return row.id

        row_id = await asyncio.to_thread(save)
        return {"added": True, "id": row_id}

    @r.post("/api/job-board/scrape")
    async def scrape_now():
        if not service.dispatch:
            raise HTTPException(409, "Start application workers before scraping")
        if not settings(db).enabled:
            raise HTTPException(409, "Enable job discovery first")
        from ...workers import enqueue_source

        started = now()

        def claim_scans():
            # Every source's scan flag is a read and a locked write; hundreds of boards ran them one by one on the
            # event loop (2026-10-29). One thread does them all.
            setup_sources(db)
            with db.session() as s:
                wanted = Source.id.in_(managed_ids(db))
                ids = list(s.scalars(select(Source.id).where(wanted, Source.enabled.is_(True))))
            claimed = []
            for sid in ids:
                scan = db.get_setting("job_board_scan:" + sid, {})
                if scan.get("state") in {"queued", "running"} and datetime.now(UTC) - datetime.fromisoformat(
                    scan.get("started_at", started)
                ) < timedelta(hours=6):
                    continue
                db.set_setting("job_board_scan:" + sid, {"state": "queued", "started_at": started})
                claimed.append(sid)
            return ids, claimed

        ids, claimed = await asyncio.to_thread(claim_scans)
        queued = 0
        for sid in claimed:
            try:
                queued += bool(await enqueue_source(sid))
            except Exception:
                await asyncio.to_thread(
                    db.set_setting,
                    "job_board_scan:" + sid,
                    {"state": "error", "started_at": started, "error": "Could not queue this source"},
                )
        await asyncio.to_thread(db.set_setting, "job_board_requested", {"at": started})
        return {"queued_sources": queued, "total_sources": len(ids)}

    @r.post("/api/job-board/apply")
    async def apply_selection(data: ApplySelection):
        if not service.dispatch:
            raise HTTPException(409, "Application workers are disabled")
        control = await asyncio.to_thread(db.get_setting, "control", {})
        if not control.get("auto_submit") or control.get("paused"):
            raise HTTPException(409, "Enable automatic submission and resume workers in Settings first")
        queued, skipped = [], []
        blocking = ACTIVE | {"confirmed", "submission_unknown", "already_applied"}

        def read_selection():
            # One thread for every read: board_snapshot scans the whole board (~0.5 s) and it ran on the event loop
            # once per selected job, up to 51 times a click, while the browser owners waited (2026-10-29).
            fresh = {j["id"] for j in board_snapshot(db)["jobs"]}
            with db.session() as s:
                busy = set(s.scalars(select(Run.job_id).where(Run.state.in_(blocking))))
                unranked = [
                    jid
                    for jid in dict.fromkeys(data.job_ids)
                    if jid in fresh and jid not in busy and s.get(Job, jid).status != "ready"
                ]
            return fresh, busy, unranked

        fresh_ids, busy, unranked = await asyncio.to_thread(read_selection)
        for offset in range(0, len(unranked), 24):
            await rank_batch(db, config, unranked[offset : offset + 24])
        if unranked:
            # Recheck expiry and activity after classification, which may take several seconds.
            fresh_ids, busy, _ = await asyncio.to_thread(read_selection)
        for jid in dict.fromkeys(data.job_ids):
            if jid not in fresh_ids:
                skipped.append({"job_id": jid, "reason": "No longer a fresh US listing"})
                continue
            if jid in busy:
                skipped.append(
                    {"job_id": jid, "reason": "Already active, submitted or awaiting confirmation"}
                )
                continue
            try:
                run = await service.queue(jid, "submit", owner_pick=True)  # The owner's own selection.
                queued.append({"job_id": jid, "run_id": run["id"]})
            except (ValueError, RuntimeError) as exc:
                skipped.append({"job_id": jid, "reason": str(exc)[:250]})
        return {"queued": queued, "skipped": skipped}

    return r
