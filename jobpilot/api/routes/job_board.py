"""Authenticated job board routes; manual application clicks use normal queue protections."""

import asyncio
import logging
import re
from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from ...db import Setting, Source, now
from ...features.discovery.tables import DcSourceHealth
from ...jobboard import LOCKED, BoardSettings, board_snapshot, fit_of, managed_ids, setup_sources, settings
from ...network import public_url
from ...schemas import SourceInput
from ..snapshot import BATCH_BOOKKEEPING


Fit = Literal["all", "eligible", "unranked", "below", "uncertain", "ready", "held"]
SELECTABLE = 500
READY_FITS = ("ready", "held")


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


class SourceLink(BaseModel):
    url: str = Field(min_length=4, max_length=2048)


# What each kind's board address looks like to the scanner: the platforms that name the employer in the path keep only that
# first part, so a pasted posting link becomes its employer's board.
BOARD_KINDS = {"greenhouse": "greenhouse", "lever": "lever", "ashby": "ashby", "smartrecruiters": "smartrecruiters", "workday": "workday"}


def source_from_link(text):
    """(name, kind, url) for a link a user pastes to a company's job board or one of its postings; ValueError says what is wrong."""
    from urllib.parse import urlsplit

    from ...ats import detect
    from ...reserved import employer_of

    link = text.strip()
    if "://" not in link:
        link = "https://" + link
    parts = urlsplit(link)
    # Words with spaces, or a word with no dot in it, are not an address: it used to be saved as a board named after itself.
    if parts.scheme not in {"http", "https"} or not parts.hostname or "." not in parts.hostname or re.search(r"\s", link):
        raise ValueError("That isn't a web link. Paste the link to the company's list of jobs, starting with https://.")
    name, _ = employer_of(link)  # a shared platform with no company in the link is refused here
    kind = BOARD_KINDS.get(detect(link).id, "career_page")
    path = [part for part in parts.path.split("/") if part]
    if kind == "workday":
        path = [part for part in path[:2] if not re.fullmatch(r"[a-z]{2}-[A-Z]{2}", part)][:1]
    elif kind != "career_page":
        path = path[:1]
    if kind == "career_page":
        return name, kind, f"https://{parts.hostname}{parts.path}"
    return name, kind, f"https://{parts.hostname}" + ("/" + "/".join(path) if path else "")


class BatchStart(BaseModel):
    target: int = Field(default=100, ge=1, le=1000)
    # The answer to the Start dialog's account-and-terms question (apply_permissions.py): "allow" turns them on, "decline" turns them off.
    permissions: Literal["allow", "decline"] | None = None


def router(service) -> APIRouter:
    db, config = service.db, service.config
    r = APIRouter()

    @r.post("/api/job-board/autopilot/start")
    async def start_autopilot(data: BatchStart):
        from ...autopilot import start_autonomous, batch_snapshot

        if not service.dispatch:
            raise HTTPException(409, "Application workers are disabled")
        if data.permissions in {"allow", "decline"}:
            from ... import apply_permissions

            (apply_permissions.grant if data.permissions == "allow" else apply_permissions.decline)(db)
        if "target" in data.model_fields_set and data.target != config.daily_application_limit:
            # The number beside Start is the daily limit (Settings › Limits › Applications per day): one value.
            from ... import connections
            from ...config import settings
            from ...autopilot import apply_daily_limit

            connections.save(config, {"daily_application_limit": str(data.target)})
            if (cached := settings()) is not config:
                connections.apply(cached)
            apply_daily_limit(db, config.daily_application_limit)
        answer = {**await start_autonomous(service, data.target), "runtime": batch_snapshot(service)}

        def lists_follow():
            """Queue, Job board and Best matches read the start at once: the line is what the dispatcher now starts by itself."""
            from ...board_view import board
            from ...queue_order import plans

            plans(service).now(service)
            board(service).stale()

        try:
            await asyncio.to_thread(lists_follow)
        except Exception:  # The batch has started. The lists catch up on their next poll.
            logging.getLogger(__name__).exception("The lists could not follow a start")
        return answer

    @r.get("/api/startable")
    def startable():
        """Ready jobs that can start, and for the rest why not (startable.py). A `def` route: it reads on a worker thread."""
        from ...startable import reading

        value = reading(service).get(service)
        # Waiting requests are in line too (queue_order.py), so this is the number of rows Up next opens.
        with db.session() as s:
            asked = s.scalar(select(func.count()).select_from(Setting).where(Setting.key.startswith("owner_apply:", autoescape=True))) or 0
        shown = {key: item for key, item in value.items() if key not in {"jobs", "ids", "details"}}
        return {**shown, "startable": value["startable"] + asked, "ready": value["ready"] + asked}

    @r.get("/api/board")
    def job_board_page(
        q: str = "", role: str = "all", company: str = "all", place: str = "all", posted: str = "all", platform: str = "all",
        fit: Literal["match", "other", "all"] = "match", sort: Literal["fit", "newest", "company"] = "fit", page: int = 1, size: int = 25,
        level: str = "all", group: bool = True, want: Literal["page", "rows", "ids"] = "page", section: Literal["main", "sites"] = "main",
    ):
        """The Job board (board_view.py): one page of open postings, filters with exact counts. A `def` route: it reads on a worker thread."""
        from ...board_view import page as board_page

        return board_page(service, q[:200], role[:200], company[:200], place[:40], posted[:4], platform[:40], fit, sort, page, size, level[:20], group, want, section)

    @r.get("/api/job-board")
    def snapshot(
        include_sources: bool = True,
        page: int | None = None,
        size: int = 25,
        q: str = "",
        ats: str = "all",
        level: str = "all",
        fit: Fit = "all",
        hold: str = "",
    ):
        """With `page`, one page of the fresh listings matching the filters and exact totals; without it, the newest
        500 as before."""
        board = board_snapshot(db, limit=None if page else 500)
        from ...reserved import held_ids

        held = held_ids(db)
        # Why the batch has not started a job (autopilot.remember_rejection): only for a job nothing has been done to
        # yet, so the Discovered list answers "why isn't this applying?" without opening the job (2026-10-29).
        rejections = db.get_setting("job_board_autopilot", {}).get("rejections") or {}
        from ...startable import reading

        # A Ready job the dispatcher will not start says why (startable.read; never read here, only what was read).
        holds = (reading(service).peek() or {}).get("jobs", {})
        for job in board["jobs"]:
            job["reserved"] = job["id"] in held
            if (why := rejections.get(job["id"])) and unapplied(job):
                job["rejection"] = why
            if (why_held := holds.get(job["id"])) and job["status"] == "ready":
                job["hold"] = why_held
        board["source_count"] = len(board["sources"])
        board["source_health"] = source_health(db, board["sources"])
        if not include_sources:
            board["sources"] = []
        board["autopilot"] = {k: v for k, v in board["autopilot"].items() if k not in BATCH_BOOKKEEPING}
        if page:
            minimum = board["settings"]["minimum_auto_fit"]
            board.update(listing_page(board["jobs"], minimum, q, ats, level, "all" if fit in READY_FITS else fit, page, size))
            value = reading(service).get(service) if fit in READY_FITS else reading(service).peek()
            if value is not None:
                # "Ready" and "Held" read the startable reading (startable.py), which also holds the jobs no board lists:
                # the numbers on the idle runners' line open exactly that many rows.
                from ...startable import rows as ready_rows

                def pick(which, code=""):
                    return listing_page(ready_rows(db, value, which, code), minimum, q, ats, level, "all", page, size)

                board["fits"] = {**board["fits"], "ready": pick("ready")["total"], "held": pick("held")["total"]}
                if fit in READY_FITS:
                    shown = pick(fit, hold)
                    board.update({k: shown[k] for k in ("jobs", "total", "page", "pages", "selectable_ids", "selectable_total")})
                board["holds"] = value["held"]

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
        # The goal is the daily limit (Settings › Limits); this form no longer carries a second number.
        data = data.model_copy(update={"batch_target": config.daily_application_limit})
        # "How close a match" is on this form and on Profile › Job preferences: one number, shown twice. Profile → board is
        # synced when the profile is saved (routes/profile.py); this is the way back, so neither screen keeps an old value. A new
        # install has no profile yet: it starts with this number, or the first profile save would put the default back.
        if settings(db).minimum_auto_fit != data.minimum_auto_fit:
            with db.exclusive() as s:
                row = s.get(Setting, "profile")
                if row:
                    row.value = {**row.value, "minimum_fit": data.minimum_auto_fit}
                else:
                    s.add(Setting(key="profile", value={"minimum_fit": data.minimum_auto_fit}))
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

    @r.post("/api/job-board/sources/link")
    async def add_source_by_link(data: SourceLink):
        """Add a company's job board from a link alone: its name, kind and board address are read from the link."""
        from ...link_intake import URL_REFUSALS

        try:
            name, kind, url = source_from_link(data.url)
            await public_url(url, config.allow_private_urls)
        except ValueError as exc:
            raise HTTPException(422, URL_REFUSALS.get(str(exc), str(exc))) from exc
        added = await add_source(SourceInput(name=name, kind=kind, url=url))
        return {**added, "name": name, "kind": kind, "url": url}

    @r.post("/api/job-board/scrape")
    async def scrape_now():
        if not service.dispatch:
            raise HTTPException(409, "Start application workers before scraping")
        if not settings(db).enabled:
            raise HTTPException(409, "Enable job discovery first")
        from ...jobboard import request_scan
        from ...workers import enqueue_source

        ids, queued = await request_scan(db, enqueue_source)
        return {"queued_sources": queued, "total_sources": len(ids)}

    return r
