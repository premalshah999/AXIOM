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
from ...discovery import add_job, posting_api_text
from ...network import public_url
from ...owner_apply import KEY, apply
from ...schemas import JobInput

MAX_LINKS = 25
LINK = re.compile(r"https?://\S+", re.I)


class QueueLinks(BaseModel):
    urls: list[str] = Field(min_length=1, max_length=MAX_LINKS)

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


JOB_COLUMNS = (Job.id, Job.title, Job.company, Job.url, Job.ats, Job.status)


def job_view(row):
    return {"job_id": row.id, "title": row.title, "company": row.company, "url": row.url, "ats": row.ats}


def router(service) -> APIRouter:
    db, config = service.db, service.config
    r = APIRouter()

    @r.get("/api/queue")
    def queue_view():
        """Running applications, then the waiting requests in the order they start (oldest first)."""
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
                for row in s.scalars(select(Setting).where(Setting.key.like(KEY + "%")))
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
        waiting.sort(key=lambda item: item["requested_at"])
        return {"active": active, "waiting": waiting}

    @r.post("/api/queue/links")
    async def queue_links(data: QueueLinks):
        """Save each pasted link and queue it as the owner's pick; one result per link, never a failed batch."""
        if not service.dispatch:
            raise ValueError("Workers are disabled")
        results = []
        for url in data.urls:
            try:
                await public_url(url, config.allow_private_urls)
                row, created = await asyncio.to_thread(add_job, db, JobInput(url=url), origin="manual")
                if created:
                    # The posting's text arrives in the background; queueing does not wait for it.
                    asyncio.create_task(posting_api_text(db, row["id"], row["url"]))
                outcome = await apply(service, row["id"])
                results.append({"url": url, "job_id": row["id"], "created": created, **outcome})
            except QueueBlocked as exc:
                results.append({"url": url, "state": "refused", "message": str(exc)})
            except (ValueError, HTTPException) as exc:
                detail = exc.detail if isinstance(exc, HTTPException) else str(exc)
                results.append({"url": url, "state": "refused", "message": str(detail)[:300]})
        return {"results": results}

    @r.delete("/api/queue/{job_id}")
    def withdraw(job_id: str):
        """Take a waiting request off the queue. A running application is paused from its own page instead."""
        with db.exclusive() as s:
            row = s.get(Setting, KEY + job_id)
            if row is None:
                raise HTTPException(status_code=404, detail="This job is not waiting in the queue")
            s.delete(row)
        return {"removed": True, "job_id": job_id}

    return r
