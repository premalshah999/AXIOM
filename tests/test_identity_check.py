"""A page that asks for a photo of a government ID or a selfie is the owner's SSN/ID rule (jobpilot/sensitive_ids.py):
the job is removed instead of left stuck. Amazon's last step (2026-10-27/29) read this way; four runs reached it. Detection
is by what the page asks, never by employer."""

import asyncio
from types import SimpleNamespace

import pytest

from jobpilot.sensitive_ids import identity_check, stop_if_sensitive

ASK = ("To verify your identity, please upload a picture of your government ID and take a photo of yourself in the "
       "verification portal.")


def page(text="", controls=(), fields=(), url="https://careers.example.test/apply"):
    return {"text": text, "url": url, "fields": list(fields),
            "controls": [{"label": c} for c in controls]}


@pytest.mark.parametrize("obs", [
    page(ASK),
    page("Step 3\nPlease upload a photo of your driver's license.\nNext"),
    page("Take a selfie so we can match it to your ID."),
    page("", controls=["Begin identity verification"]),
    page("", fields=[{"label": "I consent to identity verification", "type": "checkbox"}]),
    page("Verify your identity", url="https://flow.withpersona.com/verify?inquiry=abc"),
    page("", url="https://app.veriff.com/session/x"),
])
def test_an_id_photo_or_selfie_ask_is_an_identity_check(obs):
    assert identity_check(obs) is True


@pytest.mark.parametrize("obs", [
    page("Identity verification", controls=["Identity verification", "Review & submit"]),  # a step name only
    page("Upload a photo of your government ID (optional)."),
    page("Upload your resume and a photo of yourself is not needed."),
    page("We run background checks and identity verification purposes after an offer."),
    page("Applicants must hold a valid driver's license. " + "Our culture is open. " * 40),
    page("Upload a profile picture", fields=[{"label": "Profile photo", "type": "file"}]),
    page("", url="https://careers.example.test/id.me-article"),
])
def test_other_pages_are_not_an_identity_check(obs):
    assert identity_check(obs) is False


class Engine:
    def __init__(self, armed=False):
        self.armed, self.result, self.events = armed, None, []
        self.service = SimpleNamespace(db=None)
        self.job = {"id": "job-9", "company": "Example Corp"}

    def emit(self, kind, message, data=None):
        self.events.append((kind, message, data))


def test_the_job_is_closed_cleanly_under_the_owner_rule(monkeypatch):
    import jobpilot.job_control as job_control

    calls = []

    async def fake_manage(service, job_id, action, **kwargs):
        calls.append((job_id, action))

    monkeypatch.setattr(job_control, "manage", fake_manage)
    engine = Engine()
    assert asyncio.run(stop_if_sensitive(engine, page(ASK))) is True
    assert engine.result["state"] == "cancelled" and engine.result["blocker_type"] == "sensitive_id"
    assert engine.result["recoverable"] is False and "selfie" in engine.result["reason"]
    assert calls == [("job-9", "delete")]
    assert engine.events[0][2]["kinds"] == ["identity_check"]


def test_a_submission_already_armed_is_never_abandoned(monkeypatch):
    engine = Engine(armed=True)
    assert asyncio.run(stop_if_sensitive(engine, page(ASK))) is False and engine.result is None
