"""Runs: detail, screenshot, reconciliation, receipt check, cancellation, and answering a review."""

import json
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from ...db import Event, Job, Run, Setting, record


class AnswerInput(BaseModel):
    answer: str = Field(min_length=1, max_length=8000)


class Reconcile(BaseModel):
    submitted: bool
    evidence: str = Field(min_length=5, max_length=4000)
    # The owner submitted a parked application in the browser themselves.
    submitted_by_owner: bool = False


def router(service) -> APIRouter:
    db, config = service.db, service.config
    r = APIRouter()

    @r.post("/api/runs/{run_id}/cancel")
    async def cancel(run_id: str):
        with db.exclusive() as s:
            row = s.get(Run, run_id)
            if not row or row.state != "queued":
                raise ValueError(
                    "Only queued runs can be cancelled. Pause workers to stop active runs before commit."
                )
            row.state = "cancelled"
            s.get(Job, row.job_id).status = "cancelled"
        return {"cancelled": True}

    @r.get("/api/runs/{run_id}")
    def run_detail(run_id: str):
        with db.session() as s:
            run = s.get(Run, run_id)
            if not run:
                raise HTTPException(404)
            events = [
                record(x) for x in s.scalars(select(Event).where(Event.run_id == run_id).order_by(Event.id))
            ]
            result = {k: v for k, v in record(run).items() if k != "packet"}
            result["worker_slot"] = run.packet.get("worker_slot")
            result["worker_pool"] = run.packet.get("worker_pool")
            result["ats_specialist"] = run.packet.get("ats_specialist")
            result["application_pipeline"] = run.packet.get("application_pipeline")
            result["controller_policy"] = run.packet.get("controller_policy")
            job = s.get(Job, run.job_id)
            demo_receipt = None
            if job and job.demo:
                nonce = urlsplit(job.url).path.rsplit("/", 1)[-1]
                saved = s.get(Setting, "demo_receipt:" + nonce)
                demo_receipt = saved.value if saved else {"submissions": 0}
        folder = config.data_dir / "runs" / run_id
        ledger = (
            json.loads((folder / "answers.json").read_text()) if (folder / "answers.json").exists() else {}
        )
        return {
            "run": result,
            "events": events,
            "answers": ledger,
            "screenshot": (folder / "final.png").exists(),
            "demo_receipt": demo_receipt,
            "failure_report": db.get_setting("failure_report:" + run_id),
        }

    @r.get("/api/runs/{run_id}/screenshot")
    def screenshot(run_id: str):
        with db.session() as s:
            if not s.get(Run, run_id):
                raise HTTPException(404)
        path = config.data_dir / "runs" / run_id / "final.png"
        if not path.exists():
            raise HTTPException(404)
        return FileResponse(path, media_type="image/png")

    @r.post("/api/runs/{run_id}/reconcile")
    async def reconcile(run_id: str, data: Reconcile):
        service.reconcile(run_id, data.submitted, data.evidence, submitted_by_owner=data.submitted_by_owner)
        return {"reconciled": True}

    @r.post("/api/runs/{run_id}/check-email")
    async def check_email(run_id: str):
        from ...receipts import check_receipts

        return await check_receipts(service.mail, run_id)

    @r.post("/api/runs/{run_id}/verify")
    async def continue_verification(run_id: str):
        with db.session() as s:
            run = s.get(Run, run_id)
            if not run:
                raise HTTPException(404)
            challenge_id = run.packet.get("wait_handle", {}).get("challenge_id", "")
            if not challenge_id:
                original = s.get(Run, run.packet.get("reservation_run_id", ""))
                if original and original.job_id == run.job_id:
                    challenge_id = original.packet.get("wait_handle", {}).get("challenge_id", "")
        result = await service.resume_mail_wait(run_id, challenge_id, restart_verification=True)
        if not result:
            raise HTTPException(409, "No retained verification draft can be resumed")
        return result

    @r.post("/api/reviews/{review_id}")
    async def review(review_id: str, data: AnswerInput):
        return await service.resolve_review(review_id, data.answer)

    return r
