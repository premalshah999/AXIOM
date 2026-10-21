"""Iridium (iCIMS) 2026-10-21: ticking the email step's privacy-notice box reveals an "I accept" box, and Next stays
disabled until it is ticked. The email-step handler now agrees to boxes that appear while it waits for Next."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

from jobpilot.schemas import Answer
from support_icims import adapter

URL = "https://careers-acme.icims.com/jobs/5186/engineer/login"
EMAIL = {"id": "email", "type": "email", "label": "Email", "required": True, "value": ""}
NOTICE = {"id": "notice", "type": "checkbox", "required": False, "value": "",
          "label": "If you are a resident of California or the United Kingdom, please click here for important privacy policy information."}
ACCEPT = {"id": "accept", "type": "checkbox", "label": "I accept", "required": True, "value": ""}


def observation(fields, next_enabled):
    return {"url": URL, "fields": fields, "errors": [], "headings": ["Enter Your Information"], "text": "",
            "controls": [{"id": "next", "label": "Next", "href": "", "type": "submit", "disabled": not next_enabled,
                          "automation_id": "", "aria_label": ""}]}


async def test_a_box_revealed_by_the_first_one_is_ticked_before_next(service):
    first = observation([EMAIL, NOTICE], False)
    a = adapter(service, first)
    filled = []
    a.e.form.fill = AsyncMock(side_effect=lambda f, value: filled.append((f["id"], value)))
    a.e.form.resolver = SimpleNamespace(resolve=AsyncMock(
        side_effect=lambda fields: [Answer(field_id=fields[0]["id"], value="true", disposition="answer")]))
    a.e.form.scan = AsyncMock(side_effect=[
        observation([EMAIL, {**NOTICE, "value": "true"}, ACCEPT], False),
        observation([EMAIL, {**NOTICE, "value": "true"}, {**ACCEPT, "value": "true"}], True),
        observation([EMAIL, {**NOTICE, "value": "true"}, {**ACCEPT, "value": "true"}], True),
    ] + [observation([EMAIL], True)] * 20)
    a.challenge = {"id": "c1"}
    a.e.settle = AsyncMock()
    a.e.form.settle = AsyncMock()
    a.after_auth_dialogs = AsyncMock(return_value=None)
    a.e.form.page = SimpleNamespace(url=URL)
    await a.on_email_entry(first)
    assert ("accept", "true") in filled and ("notice", "true") in filled
