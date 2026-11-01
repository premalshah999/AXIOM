"""Jobs › Over employer limit: Ready jobs held only because their employer already has as many applications as the user's
limit (Settings; default 25 for the owner). The limit stays what the autopilot obeys. These jobs wait here for the
user's approval, as a reserved employer's jobs do (reserved.py), and Approve is the user's own Apply (owner_apply.apply):
it skips the autopilot's cap and nothing else, so the duplicate-submission guards, the truthful-answer rules and the
eligibility closures decide it as they decide any click.

Read from the same screening as the dispatcher (startable.py), on a worker thread, never on the shared event loop. A job
leaves the section the moment it is approved; the autopilot never starts one of these on its own.
"""

import asyncio

from fastapi import APIRouter, HTTPException

from ...owner_apply import MANY
from ...startable import over_limit, over_limit_count, reading

KEEP = ("id", "company", "title", "location", "ats", "url", "score", "created_at")


def router(service) -> APIRouter:
    db = service.db
    r = APIRouter()

    @r.post("/api/held-employers/release")
    async def release_employer(data: dict):
        """Try again on one held employer (loop_guard.held_employers): its jobs may start. A limit that was real comes back with
        the next refusal, and a loop with the next two failures."""
        from ...loop_guard import release

        def lift():
            with db.exclusive() as s:
                return release(s, str(data.get("key") or ""))

        if not await asyncio.to_thread(lift):
            raise HTTPException(404, "That employer is no longer held")
        await asyncio.to_thread(db.event, "", "employer_released", "Owner pressed Try again on a held employer", {"key": str(data.get("key"))[:200]})
        reading(service).at = 0.0
        return {"released": True}

    @r.get("/api/over-limit")
    def listing(q: str = "", page: int = 1, size: int = 25):
        """The jobs over the employer limit, best fit first, one page; `count` is the whole section (the tab's number)."""
        value = reading(service).get(service)
        items = over_limit(db, value, q[:200])
        size = max(1, min(100, int(size)))
        pages = max(1, -(-len(items) // size))
        page = max(1, min(int(page), pages))
        rows = [{**{k: i[k] for k in KEEP}, "hold": i["hold"]} for i in items[(page - 1) * size : page * size]]
        return {"rows": rows, "total": len(items), "count": over_limit_count(value), "page": page, "pages": pages, "size": size, "at": value["at"]}

    async def waiting():
        value = await asyncio.to_thread(reading(service).get, service)
        return [j for j, h in value["jobs"].items() if h["code"] == "employer_limit"]

    @r.post("/api/jobs/{job_id}/approve-over-limit")
    async def approve_job(job_id: str):
        """Apply to one job past the employer limit, now: the user's one-click Apply."""
        if not service.dispatch:
            raise ValueError("Application workers are disabled")
        if job_id not in await waiting():
            raise ValueError("This job is not waiting over the employer limit any more. It may already have started.")
        from ...owner_apply import apply

        result = await apply(service, job_id)
        reading(service).drop([job_id])
        await asyncio.to_thread(db.event, "", "over_limit_approval", "Owner approved applying past the employer limit", {"job_id": job_id})
        return result

    @r.post("/api/over-limit/approve-all")
    async def approve_all():
        """Apply to every job in the section (at most MANY per request, best fit first), each through the single click's
        path. Jobs a guard refuses stay in the section with the reason."""
        if not service.dispatch:
            raise ValueError("Application workers are disabled")
        from ...owner_apply import apply_many

        value = await asyncio.to_thread(reading(service).get, service)
        ids = [i["id"] for i in await asyncio.to_thread(over_limit, db, value)]
        done = await apply_many(service, ids[:MANY])
        started = [x["job_id"] for x in done["results"] if x["outcome"] != "refused"]
        reading(service).drop(started)
        await asyncio.to_thread(db.event, "", "over_limit_approval", f"Owner approved {len(started)} job(s) past the employer limit", {"count": len(started)})
        return {**done, "approved": len(started), "remaining": max(0, len(ids) - MANY)}

    return r
