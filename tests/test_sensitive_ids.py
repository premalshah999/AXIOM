"""Owner rule: an application that asks for an SSN, licence/passport number or bank details is removed.

Detection is pure. The hook test runs continuous_agent.drive() against the same scripted
fake form/engine used in test_continuous_stop.py; job_control.manage is replaced, so no
database, browser or model is needed.
"""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from jobpilot import continuous_agent as ca
from jobpilot.continuous_agent import ApplicationDecision
from jobpilot.sensitive_ids import scan_sensitive, sensitive_field


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


def test_scan_sensitive_returns_distinct_kinds_in_page_order():
    fields = [
        text("Full name"),
        text("Social Security Number"),
        {"label": "Do you have a valid driver's license?", "type": "radio"},
        text("SSN (last 4 digits)"),
        text("Driver's License Number"),
    ]
    assert scan_sensitive(fields) == ["ssn", "drivers_license_number"]
    assert scan_sensitive([text("Phone"), text("Date of Birth")]) == []


class FakeForm:
    def __init__(self, fields):
        self.resolver = SimpleNamespace(resolve=None)
        self.failed_strategies = {}
        self.buttons = {}
        self.fields = {}
        self.verified = 0
        self.fill_calls = 0
        self.obs = {
            "fields": fields,
            "progress_fingerprint": "fp-0",
            "text": "Apply to this job",
            "headings": [],
            "controls": [],
            "revision": 1,
            "owner_id": "owner",
            "busy": False,
        }

    async def scan(self):
        return dict(self.obs)

    async def settle(self, timeout=None):
        return None

    async def fill_current(self):
        self.fill_calls += 1
        return {"ok": True, "verified_fields": self.verified, "pending": []}


class FakeEngine:
    def __init__(self, form):
        self.form = form
        self.result = None
        self.armed = False
        self.service = SimpleNamespace(db=None)
        self.db = SimpleNamespace(get_setting=lambda name, default=None: default)
        self.job = {"id": "job-1", "company": "Acme", "title": "Analyst", "description": "Role"}
        self.run_record = {"packet": {"runner_tier": 1}}
        self.events = []

    def emit(self, kind, message, data=None):
        self.events.append((kind, message, data))

    async def browser_challenge(self):
        return None

    async def handle_email(self):
        return None

    async def handle_account_recovery(self):
        return False

    async def accept_policy_dialog(self, obs):
        return False

    async def recover_empty_observation(self, obs):
        return obs

    async def checkpoint_draft(self):
        return None

    def controller_blocker(self, reason="", *, original=None):
        return {"state": "needs_review", "reason": reason}

    def record_help_reviews(self, answers):
        return None


def run_drive(monkeypatch, engine, script):
    import jobpilot.browser as browser

    monkeypatch.setattr(browser, "navigation_observation", lambda obs: {})
    monkeypatch.setattr(ca, "evidence_context", lambda resolver: ({}, {}, {}))
    monkeypatch.setattr(ca, "navigation_context", lambda form, obs: {})

    async def history(form):
        return []

    monkeypatch.setattr(ca, "history_observation", history)
    payloads = []

    @asynccontextmanager
    async def fake_conversation(eng):
        async def decide(payload):
            payloads.append(payload)
            return script(len(payloads), engine.form)

        yield decide

    monkeypatch.setattr(ca, "conversation", fake_conversation)
    asyncio.run(ca.drive(engine))
    return payloads


@pytest.fixture
def manage_calls(monkeypatch):
    import jobpilot.job_control as job_control

    calls = []

    async def fake_manage(service, job_id, action, **kwargs):
        calls.append((job_id, action))
        return {"job_id": job_id, "state": "deleted"}

    monkeypatch.setattr(job_control, "manage", fake_manage)
    return calls


def fill_script(n, form):
    form.obs["progress_fingerprint"] = f"fp-{n}"
    form.fill_calls += 1
    return ApplicationDecision(action="fill")


def test_page_asking_for_ssn_stops_run_before_any_decision_and_removes_job(monkeypatch, manage_calls):
    form = FakeForm([text("Full name"), text("Social Security Number")])
    engine = FakeEngine(form)

    payloads = run_drive(monkeypatch, engine, fill_script)

    assert payloads == []  # The model is never asked to fill this page.
    assert form.fill_calls == 0
    assert engine.result["state"] == "cancelled"
    assert engine.result["blocker_type"] == "sensitive_id"
    assert engine.result["recoverable"] is False
    assert manage_calls == [("job-1", "delete")]
    (event,) = [e for e in engine.events if e[0] == "sensitive_id_requested"]
    assert event[2] == {"kinds": ["ssn"], "labels": ["Social Security Number"]}


def test_yes_no_license_question_does_not_remove_job(monkeypatch, manage_calls):
    form = FakeForm(
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
    engine = FakeEngine(form)

    payloads = run_drive(monkeypatch, engine, lambda n, f: ApplicationDecision(action="wait"))

    assert len(payloads) >= 1
    assert manage_calls == []
    assert not [e for e in engine.events if e[0] == "sensitive_id_requested"]


def test_armed_submission_is_never_stopped_by_this_rule(monkeypatch, manage_calls):
    form = FakeForm([text("Passport Number")])
    engine = FakeEngine(form)
    engine.armed = True

    payloads = run_drive(monkeypatch, engine, lambda n, f: ApplicationDecision(action="wait"))

    assert len(payloads) >= 1
    assert manage_calls == []


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
