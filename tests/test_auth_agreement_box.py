"""TikTok (2026-10-27, REA): the sign-in page's "I have read and agree to ..." box has no label; with it unticked,
Sign in sent nothing and four runs read the unchanged form as a failed sign-in."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

from support_icims import adapter

URL = "https://careers-acme.icims.com/jobs/42/x/login"


def box(fid, text):
    return {"id": fid, "type": "checkbox", "label": "", "value": "", "required": False, "_text": text}


async def run(service, accept, fields):
    obs = {"url": URL, "fields": fields, "controls": [], "errors": [], "headings": [], "text": ""}
    value = adapter(service, obs)
    filled = []
    value.form.fill = AsyncMock(side_effect=lambda f, v: filled.append(f["id"]))
    texts = {f["id"]: f["_text"] for f in fields}
    value.form.locator = lambda fid: SimpleNamespace(evaluate=AsyncMock(return_value=texts[fid]))
    value.emit = lambda *a, **k: None
    await value.tick_auth_agreements(obs, SimpleNamespace(accept_all_application_terms=accept))
    return filled


async def test_the_terms_box_is_ticked_when_terms_are_pre_approved(service):
    fields = [box("terms", "I have read and agree to User Agreement and Applicant Privacy Policy"),
              box("alerts", "Send me job alerts"), box("remember", "Remember me")]
    assert await run(service, True, fields) == ["terms"]


async def test_nothing_is_ticked_without_the_applicants_approval(service):
    assert await run(service, False, [box("terms", "I agree to the Terms of Use")]) == []
