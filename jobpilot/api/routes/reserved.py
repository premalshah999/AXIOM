"""Jobs › Reserved: FAANG, MANGO and the Big Four wait for the owner's approval (owner, 2026-10-27: "make a different
section for FAANG, MANGO, Big four consulting, and these scrapers should not apply before my permission").

The list of reserved employers is the owner's setting (reserved.py). Approving one job lifts its hold and, unless asked
not to, applies at once through the owner's one-click Apply. Approving a whole section lifts the hold only: those jobs
then join the pipeline like any other employer's, and the autopilot applies to the ones that fit the profile.
"""

from fastapi import APIRouter
from pydantic import BaseModel, Field

from ... import reserved
from ...db import Run, now
from ...service.queueing import off_loop
from .. import pipeline


class Approve(BaseModel):
    # False: lift the hold only; the autopilot decides as for any other employer.
    apply: bool = True


class ApproveAll(BaseModel):
    # A section id ("faang"); none approves every held job.
    section: str | None = Field(default=None, max_length=32)


def router(service) -> APIRouter:
    db = service.db
    r = APIRouter()

    def view(section="", q="", page=1, size=25):
        with db.session() as s:
            result = pipeline.reserved_page(s, section, q[:200], page, size)
            result["rules"] = reserved.rules(s).model_dump()
        return result

    @r.get("/api/reserved")
    def listing(section: str = "", q: str = "", page: int = 1, size: int = 25):
        """One section's held jobs (newest first), every section's count and the saved employer list."""
        return view(section, q, page, size)

    @r.put("/api/reserved")
    def save(data: reserved.Rules):
        """Replace the reserved employers. Jobs that stop matching are released at once; new matches are held."""
        db.set_setting(reserved.KEY, {**data.model_dump(), "updated_at": now()})
        return view()

    def approve_one(job_id):
        from sqlalchemy import select

        with db.exclusive() as s:
            found = reserved.approve(s, job_id)
            last = s.scalar(select(Run.id).where(Run.job_id == job_id).order_by(Run.created_at.desc()).limit(1))
        db.event(last or "", "reserved_approval", f"Owner approved applying to {found.employer} ({found.label})",
                 {"job_id": job_id, **found.as_dict()})
        return found

    @r.post("/api/jobs/{job_id}/approve-reserved")
    async def approve_job(job_id: str, data: Approve | None = None):
        """The owner's permission for one reserved job, then (by default) the one-click Apply."""
        found = await off_loop(approve_one, job_id)
        result = {"job_id": job_id, "approved": True, **found.as_dict()}
        if data is not None and not data.apply:
            return {**result, "state": "approved", "message": f"Approved. The autopilot may now apply to this {found.employer} job."}
        if not service.dispatch:
            return {**result, "state": "approved", "message": "Approved. Application workers are disabled, so it has not started."}
        from ...owner_apply import apply

        try:
            started = await apply(service, job_id)
        except ValueError as exc:  # QueueBlocked included: the approval stands, the start did not happen.
            return {**result, "state": "approved", "message": f"Approved, but it did not start: {str(exc)[:300]}"}
        return {**result, **started}

    @r.delete("/api/jobs/{job_id}/approve-reserved")
    def revoke_job(job_id: str):
        """Withdraw the permission: the job is held again."""
        with db.exclusive() as s:
            removed = reserved.revoke(s, job_id)
        return {"job_id": job_id, "approved": False, "removed": removed}

    @r.post("/api/reserved/approve-all")
    def approve_all(data: ApproveAll):
        """Lift the hold on every job waiting in a section (or in all sections), the jobs its count shows. Nothing
        starts here."""
        with db.exclusive() as s:
            ids = [
                job_id
                for job_id, _, found in pipeline.awaiting_approval(s)
                if data.section is None or found.section == data.section
            ]
            for job_id in ids:
                reserved.approve(s, job_id)
        db.event("", "reserved_approval", f"Owner approved {len(ids)} reserved job(s)",
                 {"section": data.section, "count": len(ids)})
        return {"approved": len(ids), "section": data.section}

    return r
