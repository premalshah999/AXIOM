"""The application queue: what is running, what starts next and why it waits, and pasted links that go straight in.

Owner, 2026-10-22: "a new section of queued applications, and an option to drop links, so if I paste a link it gets
in the pipeline, on the queue itself." A pasted link is the owner's own pick: it is approved and queued like a click
on Apply, without the fit ranking that made the old "Add job link" slow and could leave the job unqueued, and without
the autopilot's fit filters (target roles, career level, employer cap, eligibility). Duplicate submission protection
and the platform's pacing still apply at the queue.
"""

import asyncio
import re

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select

from ...admission import QueueBlocked
from ...db import Job, Run, Setting
from ...discovery import add_job, read_added_posting
from ...link_intake import URL_REFUSALS, prepare
from ...network import public_url
from ...owner_apply import KEY, REFUSED_HOLDS, apply, restore
from ...schemas import JobInput

MAX_LINKS = 25
LINK = re.compile(r"https?://\S+", re.I)


class QueueLinks(BaseModel):
    urls: list[str] = Field(min_length=1, max_length=MAX_LINKS)
    tailor_mode: str | None = Field(default=None, max_length=40)  # Apply with a tailored resume, in this mode. None is plain Apply, the default.

    @field_validator("urls")
    @classmethod
    def links_only(cls, urls):
        cleaned = list(dict.fromkeys(u.strip().rstrip(".,;)") for u in urls if u.strip()))
        if not cleaned:
            raise ValueError("Paste at least one job link")
        bad = [u for u in cleaned if not LINK.fullmatch(u) or len(u) > 2048]
        if bad:
            raise ValueError(f"Not a job link: {bad[0][:120]}")
        return cleaned


class ManyJobs(BaseModel):
    # A Select all across every page. Nothing starts here, so this isn't the Apply path's limit of fifty a request (owner_apply.MANY):
    # each job is one write, and a board of hundreds refused with a 422 is what "select all" must never do.
    job_ids: list[str] = Field(min_length=1, max_length=5000)


class Accept(BaseModel):
    batch_id: str = Field(min_length=1, max_length=64)
    job_ids: list[str] = Field(default_factory=list, max_length=100)
    unticked: list[str] = Field(default_factory=list, max_length=100)


class Size(BaseModel):
    size: int = Field(ge=5, le=100)


JOB_COLUMNS = (Job.id, Job.title, Job.company, Job.url, Job.ats, Job.status)


def job_view(row):
    return {"job_id": row.id, "title": row.title, "company": row.company, "url": row.url, "ats": row.ats}


def router(service) -> APIRouter:
    db, config = service.db, service.config
    r = APIRouter()

    @r.get("/api/queue")
    def queue_view(page: int = 1, size: int = 25):
        """Queue: what is running with the step it is on, then every job the dispatcher will start, in the order it starts them
        (queue_order.py), one page at a time; then what it is retrying by itself. A `def` route: this reads on a worker thread."""
        from ...queue_order import active_rows, dispatching, plans

        planner = plans(service)
        value = planner.ready(service)
        control = db.get_setting("control", {}) or {}
        board = db.get_setting("job_board_settings", {}) or {}
        running = active_rows(service)
        if value is None:
            # A cold start: what is running paints now, the line follows on the next poll.
            return {
                "at": "", "planning": True, "paused": bool(control.get("paused")), "auto_submit": bool(control.get("auto_submit")),
                "auto_apply": bool(board.get("enabled") and board.get("auto_apply")), "dispatching": dispatching(db), "stopped": None,
                "workers": {**{k: getattr(config.topology, k) for k in ("normal", "specialist", "custom")}, "free": 0},
                "decides": "", "exact": 0, "running": running, "next": [], "total": 0, "page": 1, "pages": 1, "size": size,
                "retrying": planner.retrying(service), "held": [], "held_total": 0, **_requests(),
            }
        items = value["next"]
        size = max(1, min(100, int(size)))
        pages = max(1, -(-len(items) // size))
        page = max(1, min(int(page), pages))
        drop = {"employer", "lane", "details", "status", "tailoring", "not_before", "front", "paced"}
        reading = value["reading"]
        from ...features.resumes.choice import waiting_words

        shown = items[(page - 1) * size : page * size]
        tailoring = waiting_words(db, [item["id"] for item in shown])
        shown = [{**item, "wait": tailoring[item["id"]]} if item["id"] in tailoring else item for item in shown]
        return {
            "at": value["at"],
            "paused": bool(control.get("paused")),
            "auto_submit": bool(control.get("auto_submit")),
            "auto_apply": bool(board.get("enabled") and board.get("auto_apply")),
            "dispatching": dispatching(db),
            "stopped": reading.get("stopped"),
            "workers": {**{k: getattr(config.topology, k) for k in ("normal", "specialist", "custom")}, "free": value["free"]},
            "decides": value["decides"],
            "exact": value["exact"],
            "running": running,
            "next": [{k: v for k, v in item.items() if k not in drop} for item in shown],
            "total": len(items),
            "page": page,
            "pages": pages,
            "size": size,
            "retrying": planner.retrying(service),
            "held": reading.get("held", []),
            "held_total": sum(h["count"] for h in reading.get("held", [])),
            **_requests(),
        }

    def _requests():
        """The waiting Apply clicks by themselves, oldest first, and the started applications."""
        with db.session() as s:
            active = [
                {**job_view(row), "run_id": row.run_id, "state": row.state, "since": row.started_at or row.created_at}
                for row in s.execute(
                    select(*JOB_COLUMNS, Run.id.label("run_id"), Run.state, Run.started_at, Run.created_at)
                    .join(Run, Run.job_id == Job.id)
                    .where(Run.state.in_(["queued", "running", "submitting"]))
                    .order_by(Run.created_at)
                )
            ]
            requests = {
                row.key.removeprefix(KEY): dict(row.value or {})
                for row in s.scalars(select(Setting).where(Setting.key.startswith(KEY, autoescape=True)))
            }
            jobs = {
                row.id: row for row in s.execute(select(*JOB_COLUMNS).where(Job.id.in_(list(requests))))
            }
        running = {item["job_id"] for item in active}
        waiting = [
            {
                **job_view(jobs[job_id]),
                "requested_at": value.get("at", ""),
                "code": value.get("code", ""),
                "reason": value.get("reason", ""),
                "not_before": value.get("not_before", ""),
            }
            for job_id, value in requests.items()
            if job_id in jobs and job_id not in running
        ]
        from ...features.resumes.choice import waiting_words

        # The reason is what the last try found, so it can still say "Preparing" after the resume was made.
        words = waiting_words(db, [item["id"] for item in waiting])
        waiting = [{**item, "reason": words[item["id"]]} if item["id"] in words else item for item in waiting]
        waiting.sort(key=lambda item: item["requested_at"])
        return {"active": active, "waiting": waiting}

    @r.get("/api/queue/held")
    def queue_held(code: str = "", page: int = 1, size: int = 25):
        """Found and Ready but not in line, each with why: the same rows Overview's "held" number counts (startable.rows)."""
        from ...startable import reading as read_held, rows as ready_rows

        value = read_held(service).get(service)
        items = ready_rows(db, value, "held", code[:40])
        size = max(1, min(100, int(size)))
        pages = max(1, -(-len(items) // size))
        page = max(1, min(int(page), pages))
        keep = ("id", "title", "company", "url", "ats", "location", "score", "hold")
        return {
            "rows": [{k: item.get(k) for k in keep} for item in items[(page - 1) * size : page * size]],
            "total": len(items), "page": page, "pages": pages, "size": size, "held": value.get("held", []),
            "selectable": sum(1 for item in items if item["hold"]["code"] not in REFUSED_HOLDS and item["hold"]["code"] != "reserved"),
        }

    def changed(*job_ids):
        """Every list reads the move at once: the reading forgets the jobs, and the next poll replays the line."""
        from ...board_view import board
        from ...queue_order import plans
        from ...startable import reading as read_held

        read_held(service).drop(list(job_ids))
        plans(service).now(service)
        board(service).stale()

    @r.post("/api/queue/{job_id}/front")
    def to_front(job_id: str):
        """Move a job to the front of the line, or put it in line there. Nothing starts here: the dispatcher starts it in its turn,
        so this can't run past a Stop, and every guard still decides when its turn comes."""
        from ...owner_apply import enqueue

        result = enqueue(service, job_id, front=True)
        changed(job_id)
        return result

    @r.post("/api/queue/{job_id}/add")
    def add_to_queue(job_id: str):
        """Put a job from the board in line, as the user's own pick: at the end of what they asked for, ahead of the rest."""
        from ...owner_apply import enqueue

        result = enqueue(service, job_id)
        changed(job_id)
        return result

    def many(job_ids, front=False):
        """Put the ticked jobs in line, each decided as its own click (owner_apply.enqueue_many); `message` says what became of them."""
        from ...owner_apply import enqueue_many

        out = enqueue_many(service, job_ids, front=front)
        if out["added"]:
            changed(*out["added"])
        added, already, refused = len(out["added"]), len(out["already"]), len(out["refused"])
        parts = [f"{added:,} {'job' if added == 1 else 'jobs'} {'moved to the front of' if front else 'added to'} the queue."] if added or not refused else []
        if already:
            parts.append(f"{already:,} already in line, running or applied.")
        if refused:
            parts.append(f"{refused:,} couldn't be queued. Each says why.")
        return {"added": added, "already": already, "refused": out["refused"], "message": " ".join(parts)}

    @r.post("/api/queue/add-many")
    def add_many(data: ManyJobs):
        return many(data.job_ids)

    @r.post("/api/queue/front-many")
    def front_many(data: ManyJobs):
        return many(data.job_ids, front=True)

    @r.get("/api/queue/ids")
    def queue_ids():
        """Every job in line, in order: what Select all on Up next selects, whichever page is showing."""
        from ...queue_order import plans

        value = plans(service).ready(service)
        return {"ids": [item["id"] for item in (value or {}).get("next", [])]}

    @r.get("/api/queue/held/ids")
    def held_ids(code: str = ""):
        """Every job held back for this reason: what Select all on Held back selects."""
        from ...startable import reading as read_held, rows as ready_rows

        items = ready_rows(db, read_held(service).get(service), "held", code[:40])
        return {"ids": [item["id"] for item in items if item["hold"]["code"] not in REFUSED_HOLDS and item["hold"]["code"] != "reserved"]}

    @r.get("/api/review-batch")
    def review_batch():
        """The open review batch, gathered when there is none. A `def` route: this reads on a worker thread."""
        from ...review_batch import current

        return current(service)

    @r.post("/api/review-batch/accept")
    def review_accept(data: Accept):
        """Queue the ticked jobs of this batch and prepare the next."""
        from ...review_batch import accept

        return accept(service, data.batch_id, data.job_ids, data.unticked, lambda ids: changed(*ids))

    @r.post("/api/review-batch/size")
    def review_size(data: Size):
        from ...review_batch import resize

        return resize(service, data.size)

    @r.post("/api/queue/{job_id}/back")
    async def back_in_queue(job_id: str):
        """Put a job you removed back in line. Nothing starts here. One with earlier attempts resumes as its own page's Resume does."""
        from ...job_control import manage
        from ...owner_apply import back_in_line

        def attempted():
            with db.session() as s:
                return s.scalar(select(Run.id).where(Run.job_id == job_id).limit(1)) is not None

        if await asyncio.to_thread(attempted):
            result = await manage(service, job_id, "resume")
        else:
            result = await asyncio.to_thread(back_in_line, service, job_id)
        await asyncio.to_thread(changed, job_id)
        return result

    @r.post("/api/queue/links")
    async def queue_links(data: QueueLinks):
        """Save each pasted link and queue it as the owner's pick; one result per link, never a failed batch."""
        if not service.dispatch:
            raise ValueError("Workers are disabled")
        results = []
        for url in data.urls:
            try:
                await public_url(url, config.allow_private_urls)
                # A wrapper (LinkedIn's outbound page, a search redirect) is the employer's link inside it; a board page
                # that gives none, or a list of jobs, stops here with what to do (link_intake).
                link, problem = await prepare(url, config.allow_private_urls)
                if problem:
                    results.append({"url": url, "state": "refused", "message": problem})
                    continue
                if link != url:
                    await public_url(link, config.allow_private_urls)
                row, created = await asyncio.to_thread(add_job, db, JobInput(url=link), origin="manual")
                if created:
                    # The posting's text arrives in the background; queueing does not wait for it.
                    asyncio.create_task(read_added_posting(db, config, row["id"]))
                if data.tailor_mode and data.tailor_mode != "off":
                    # Before it's queued, or a free worker would send the uploaded resume first.
                    from ...features.resumes import choice as tailor_choice

                    if await asyncio.to_thread(tailor_choice.choose, db, row["id"], data.tailor_mode):
                        tailor_choice.start(db, config, row["id"])
                if row["status"] == "archived":
                    # Pasting the link again is the owner asking for it again. A removed job comes back unless an
                    # application may have reached the employer or it asked for an identifier (restore says which).
                    await asyncio.to_thread(restore, service, row["id"])
                outcome = await apply(service, row["id"])
                results.append({"url": url, "job_id": row["id"], "created": created, **outcome, **({"resolved_url": link} if link != url else {})})
            except QueueBlocked as exc:
                results.append({"url": url, "state": "refused", "message": str(exc)})
            except LookupError as exc:  # The mode isn't available, or this job has already started.
                results.append({"url": url, "state": "refused", "message": str(exc)[:300]})
            except (ValueError, HTTPException) as exc:
                detail = exc.detail if isinstance(exc, HTTPException) else str(exc)
                results.append({"url": url, "state": "refused", "message": URL_REFUSALS.get(str(detail), str(detail))[:300]})
        return {"results": results}

    async def take_out(job_id):
        """Out of the line, for good until the user puts it back: its waiting request goes, and a pause keeps the dispatcher from starting
        it again by itself (removing only the request left a Ready job for the next tick to start). False when it wasn't in line."""
        from ...job_control import manage
        from ...startable import reading as read_held

        def waiting():
            with db.exclusive() as s:
                row = s.get(Setting, KEY + job_id)
                job = s.get(Job, job_id)
                if row is not None:
                    s.delete(row)
                in_line = bool(job and job.status == "ready" and job.id in (read_held(service).peek() or {}).get("ids", ()))
                return row is not None or in_line

        if not await asyncio.to_thread(waiting):
            return False
        await manage(service, job_id, "pause")
        return True

    @r.delete("/api/queue/{job_id}")
    async def withdraw(job_id: str):
        """Take a job out of the line. A running application is stopped from its own page instead."""
        if not await take_out(job_id):
            raise HTTPException(status_code=404, detail="This job is not waiting in the queue")
        await asyncio.to_thread(changed, job_id)
        return {"removed": True, "job_id": job_id}

    @r.post("/api/queue/remove-many")
    async def remove_many(data: ManyJobs):
        """Take the ticked jobs out of the line; one that has already started is left to its own page, and counted as such."""
        removed = [job_id for job_id in dict.fromkeys(data.job_ids) if await take_out(job_id)]
        if removed:
            await asyncio.to_thread(changed, *removed)
        return {"removed": len(removed), "not_in_line": len(set(data.job_ids)) - len(removed)}

    return r
