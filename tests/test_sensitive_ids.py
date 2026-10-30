"""Owner rule: an application that asks for an SSN, licence/passport number or bank details is removed.

Detection is pure. The hook test calls stop_if_sensitive() on a scripted fake engine; job_control.manage is
replaced, so no database, browser or model is needed.
"""

import asyncio
from types import SimpleNamespace

import pytest

from jobpilot.sensitive_ids import sensitive_field, stop_if_sensitive


def text(label):
    return {"id": "x", "label": label, "type": "text", "required": True, "value": ""}


@pytest.mark.parametrize(
    "label, kind",
    [
        ("Social Security Number", "ssn"),
        ("SSN (last 4 digits)", "ssn"),
        ("Driver's License Number", "drivers_license_number"),
        ("Passport Number", "passport_number"),
        ("National ID", "national_id"),
        ("Bank account number", "bank_account"),
        ("Routing number", "bank_account"),
        ("Social Insurance Number", "sin"),
        ("Taxpayer identification number", "taxpayer_id"),
    ],
)
def test_identifier_text_fields_are_detected(label, kind):
    assert sensitive_field(text(label)) == kind
    assert sensitive_field({**text(label), "type": "password"}) == kind
    assert sensitive_field({**text(label), "type": "number"}) == kind


@pytest.mark.parametrize(
    "field",
    [
        {"label": "Do you have a valid driver's license?", "type": "radio"},
        {"label": "Do you have a valid driver's license?", "type": "select"},
        {"label": "Do you have a valid driver's license?", "type": "text"},
        {"label": "Date of Birth", "type": "text"},
        {"label": "Date of Birth", "type": "date"},
        {"label": "Are you legally authorized to work in the United States?", "type": "radio"},
        {"label": "Are you legally authorized to work in the United States?", "type": "text"},
        {"label": "Social Security Number", "type": "select"},
        {"label": "Driver's License Number", "type": "checkbox"},
        {"label": "Can you provide proof of eligibility?", "type": "file"},
        {"label": "Professional license number", "type": "text"},
        {"label": "Social Security benefits", "type": "radio"},
        {
            "label": "Please describe your experience with passports and visas for our clients. " * 3,
            "type": "text",
        },
    ],
)
def test_yes_no_dates_and_non_identifiers_are_not_detected(field):
    assert sensitive_field(field) is None


class FakeEngine:
    def __init__(self, fields, armed=False):
        self.armed = armed
        self.result = None
        self.service = SimpleNamespace(db=None)
        self.job = {"id": "job-1", "company": "Acme", "title": "Analyst", "description": "Role"}
        self.events = []
        self.obs = {"fields": fields, "text": "Apply to this job"}

    def emit(self, kind, message, data=None):
        self.events.append((kind, message, data))


@pytest.fixture
def manage_calls(monkeypatch):
    import jobpilot.job_control as job_control

    calls = []

    async def fake_manage(service, job_id, action, **kwargs):
        calls.append((job_id, action))
        return {"job_id": job_id, "state": "deleted"}

    monkeypatch.setattr(job_control, "manage", fake_manage)
    return calls


def test_page_asking_for_ssn_stops_run_and_removes_job(manage_calls):
    engine = FakeEngine([text("Full name"), text("Social Security Number")])

    assert asyncio.run(stop_if_sensitive(engine, engine.obs)) is True

    assert engine.result["state"] == "cancelled"
    assert engine.result["blocker_type"] == "sensitive_id"
    assert engine.result["recoverable"] is False
    assert manage_calls == [("job-1", "delete")]
    (event,) = [e for e in engine.events if e[0] == "sensitive_id_requested"]
    assert event[2] == {"kinds": ["ssn"], "labels": ["Social Security Number"]}


def test_yes_no_license_question_does_not_remove_job(manage_calls):
    engine = FakeEngine(
        [
            {
                "id": "r1",
                "label": "Do you have a valid driver's license?",
                "type": "radio",
                "required": True,
                "value": "",
                "valid": True,
            }
        ]
    )

    assert asyncio.run(stop_if_sensitive(engine, engine.obs)) is False

    assert manage_calls == []
    assert not [e for e in engine.events if e[0] == "sensitive_id_requested"]


def test_armed_submission_is_never_stopped_by_this_rule(manage_calls):
    engine = FakeEngine([text("Passport Number")], armed=True)

    assert asyncio.run(stop_if_sensitive(engine, engine.obs)) is False

    assert manage_calls == []
    assert engine.result is None


def test_removed_job_is_never_escalated_or_retried():
    from jobpilot.service import RETRYABLE, TERMINAL
    from jobpilot.tiers import BLOCKED_JOB, FAILED

    assert "cancelled" in TERMINAL and "cancelled" not in RETRYABLE
    assert "cancelled" not in FAILED  # Tier escalation only retries waiting_browser/timed_out/failed.
    assert "archived" in BLOCKED_JOB  # manage(..., "delete") archives the job; candidates skip it.


def test_long_ssn_paragraph_and_indian_ids_are_sensitive():
    from jobpilot.sensitive_ids import sensitive_field

    bcbst = ("For pre-screening and identity verification purposes, BlueCross requests the last four digits of your "
             "Social Security Number. This information will be used to confirm your prior employment status and will "
             "be kept confidential in accordance with our privacy practices.")
    assert sensitive_field({"type": "text", "label": bcbst}) == "ssn"
    assert sensitive_field({"type": "text", "label": "Please confirm if you have a valid Aadhar card."}) == "national_id"
    assert sensitive_field({"type": "text", "label": "Please confirm if you have a valid PAN card."}) == "national_id"
    # Long help paragraphs that merely mention benefits are still not the field's question.
    long_help = "We value privacy. " * 12 + "Social Security benefits are described in the handbook."
    assert sensitive_field({"type": "text", "label": long_help}) is None
