"""Runs: detail, screenshot, reconciliation, receipt check, cancellation, and answering a review."""

import json
from urllib.parse import urlsplit

from fastapi import APIRouter, File, HTTPException, UploadFile
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
    # The parked run's own final page shows the employer's acknowledgement (quote it in evidence).
    final_page: bool = False


def router(service) -> APIRouter:
    db, config = service.db, service.config
    r = APIRouter()

    @r.get("/api/runs")
    def job_runs(job_id: str):
        """Every attempt of one job, newest first (the job panel's Attempts list), without packets."""
        from ..pipeline import runs_of

        with db.session() as s:
            return {"runs": runs_of(s, job_id)}

    @r.get("/api/runs/{run_id}")
    def run_detail(run_id: str):
        with db.session() as s:
            # Five packet keys, not the whole ~320 KB packet: a live run's panel asks every 10 s (2026-10-22).
            found = s.execute(
                select(
                    *(column for column in Run.__table__.columns if column.name != "packet"),
                    Run.packet["worker_slot"].as_integer().label("worker_slot"),
                    Run.packet["worker_pool"].as_string().label("worker_pool"),
                    Run.packet["ats_specialist"].as_string().label("ats_specialist"),
                    Run.packet["application_pipeline"].as_string().label("application_pipeline"),
                    Run.packet["controller_policy"].as_string().label("controller_policy"),
                ).where(Run.id == run_id)
            ).first()
            if not found:
                raise HTTPException(404)
            events = [
                record(x) for x in s.scalars(select(Event).where(Event.run_id == run_id).order_by(Event.id))
            ]
            result = dict(found._mapping)
            run = found
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
    def reconcile(run_id: str, data: Reconcile):
        service.reconcile(
            run_id, data.submitted, data.evidence, submitted_by_owner=data.submitted_by_owner, final_page=data.final_page
        )
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

    @r.get("/api/reviews/{review_id}/screenshot")
    def review_screenshot(review_id: str):
        """The redacted capture made for this question (its field, centred and outlined), as Telegram sends it."""
        from fastapi.responses import Response

        from ...db import Review
        from ...diagnostics import current_help_bytes

        with db.session() as s:
            review = s.get(Review, review_id)
            if not review:
                raise HTTPException(404)
            run_id, question, key = review.run_id, review.question, review.key
            field_id = (review.context or {}).get("field_id")
        picture = current_help_bytes(service, run_id, question=question, review_key=key, field_id=field_id)
        if picture is None:
            raise HTTPException(404)
        return Response(picture, media_type="image/jpeg")

    @r.post("/api/reviews/{review_id}/file")
    async def review_file(review_id: str, file: UploadFile = File(...)):
        """An upload question is answered with a file, kept for later forms asking for the same document."""
        from ...owner_questions import MAX_DOCUMENT_BYTES

        content = await file.read(MAX_DOCUMENT_BYTES + 1)
        return await service.resolve_review_file(review_id, file.filename or "document", content)

    return r
