"""The synthetic employer form and the demo application behind `jobpilot verify`."""

import hashlib

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select

from ...db import Resume, Setting, now
from ...schemas import QueueInput


def router(service) -> APIRouter:
    db, config = service.db, service.config
    r = APIRouter()

    @r.post("/api/demo")
    async def demo(data: QueueInput):
        if not config.enable_demo or not service.dispatch:
            raise ValueError("Demo or workers are disabled")
        from ...demo import create_demo

        row = await create_demo(service, data.mode)
        return {k: v for k, v in row.items() if k != "packet"}

    @r.get("/demo/form/{nonce}", response_class=HTMLResponse)
    def demo_form(nonce: str):
        from ...demo import FORM, valid_demo

        if not config.enable_demo or not valid_demo(db, "/demo/form/" + nonce):
            raise HTTPException(404)
        return FORM

    @r.post("/demo/form/{nonce}", response_class=HTMLResponse)
    async def demo_submit(nonce: str, request: Request):
        from ...demo import valid_demo

        if not config.enable_demo or not valid_demo(db, "/demo/form/" + nonce):
            raise HTTPException(404)
        data = await request.form()
        expected = {
            "first_name": "Alex",
            "last_name": "Example",
            "email": "alex@example.test",
            "phone": "2025550100",
            "city": "New York",
            "gender": "Prefer not to identify",
        }
        if any(data.get(key) != value for key, value in expected.items()):
            raise HTTPException(422, "Synthetic test fields do not match the expected applicant")
        upload = data.get("resume")
        if not hasattr(upload, "read"):
            raise HTTPException(422, "Synthetic test resume missing")
        content = await upload.read(5 * 1024 * 1024 + 1)
        digest = hashlib.sha256(content).hexdigest()
        with db.exclusive() as s:
            resume = s.scalar(select(Resume).where(Resume.demo.is_(True)))
            if not resume or digest != resume.sha256:
                raise HTTPException(422, "Synthetic test resume checksum does not match")
            saved = s.get(Setting, "demo_receipt:" + nonce)
            receipt = {
                "submissions": (saved.value["submissions"] if saved else 0) + 1,
                "resume_sha256": digest,
                "received_at": now(),
            }
            if saved:
                saved.value = receipt
            else:
                s.add(Setting(key="demo_receipt:" + nonce, value=receipt))
        return (
            "<h1>Thank you for applying!</h1><p>Your application has been received.</p><p>Demo receipt: "
            + nonce
            + "</p>"
        )

    return r
