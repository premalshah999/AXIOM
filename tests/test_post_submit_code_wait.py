"""Greenhouse emailed-code step after the final submit click.

watch_submission() is driven by scripted fakes (form observations, proof, the email helper),
so no browser, database or mailbox is needed. The close() tests use the real request row.
"""

from types import SimpleNamespace

import pytest

from jobpilot import browser
from jobpilot.browser import BrowserEngine
from jobpilot.db import MailChallenge
from jobpilot.email_browser import EmailBrowser
from support_mail import configured

RECEIPT = {"confirmation": "Thank you for applying"}


@pytest.fixture(autouse=True)
def fast_polls(monkeypatch):
    monkeypatch.setattr(browser, "POST_SUBMIT_POLL_SECONDS", 0.001)
    monkeypatch.setattr(browser, "POST_SUBMIT_CODE_SETTLE_SECONDS", 0.2)


class FakeForm:
    """code_at / receipt_at are scan counts; one scan happens per post-click poll."""

    def __init__(self, *, code_at=None, receipt_at=None):
        self.code_at, self.receipt_at = code_at, receipt_at
        self.scans = 0
        self.cleared = False  # the employer accepted the code and left the code step
        self.receipt_granted = False

    async def scan(self):
        self.scans += 1
        code_page = self.code_at is not None and self.scans >= self.code_at and not self.cleared
        return {"code_page": code_page, "fields": [], "controls": [], "text": "", "headings": []}

    async def proof(self):
        if self.receipt_granted or (self.receipt_at is not None and self.scans >= self.receipt_at):
            return dict(RECEIPT)
        return None

    async def rejection(self):
        return None


class FakeEmail:
    def __init__(self, form, *, challenge_id="challenge-1", accepted_gives_receipt=True):
        self.form, self.challenge_id = form, challenge_id
        self.accepted_gives_receipt = accepted_gives_receipt
        self.attempts = 0
        self.deferred = False
        self.extended = False

    def extend_pre_armed_request(self):
        self.extended = True

    def state(self, observation):
        return ("code", [{"id": "code"}]) if observation["code_page"] else (None, [])

    async def handle(self):
        if self.attempts:
            raise ValueError("Email verification did not complete; automatic resends are disabled")
        self.attempts += 1
        self.form.cleared = True
        if self.accepted_gives_receipt:
            self.form.receipt_granted = True
        return True


def engine(form, email, ats="greenhouse"):
    e = object.__new__(BrowserEngine)
    e.form, e.email_verification = form, email
    e.job = {"ats": ats, "demo": False, "url": "https://job-boards.greenhouse.io/example/jobs/1"}
    e.run_record = {"packet": {"resume_sha": "resume-sha"}}
    e.armed = True
    e.emit = lambda *args, **kwargs: None
    return e


async def watch(e):
    return await e.watch_submission(before=None, before_rejection=None)


async def test_code_step_after_receipt_window_is_entered_and_its_receipt_confirms():
    # The emailed-code page renders after the 60-poll receipt window (Greenhouse validates first).
    form = FakeForm(code_at=70)
    email = FakeEmail(form)
    result = await watch(engine(form, email))
    assert email.extended and email.attempts == 1
    assert result["state"] == "confirmed", result
    assert result["receipt"]["resume_sha256"] == "resume-sha"


async def test_code_step_accepted_without_receipt_is_not_confirmed():
    form = FakeForm(code_at=70)
    email = FakeEmail(form, accepted_gives_receipt=False)
    result = await watch(engine(form, email))
    assert email.attempts == 1
    assert result["state"] == "submission_unknown", result


async def test_no_code_page_is_bounded_and_stays_submission_unknown():
    form = FakeForm()
    email = FakeEmail(form)
    result = await watch(engine(form, email))
    assert email.attempts == 0
    assert result["state"] == "submission_unknown"
    assert form.scans >= browser.POST_SUBMIT_POLLS


async def test_receipt_inside_the_window_confirms_without_touching_the_code_helper():
    form = FakeForm(receipt_at=5)
    email = FakeEmail(form)
    result = await watch(engine(form, email))
    assert result["state"] == "confirmed"
    assert email.attempts == 0


async def test_only_greenhouse_waits_past_the_receipt_window_for_a_code():
    form = FakeForm(code_at=70)
    email = FakeEmail(form)
    result = await watch(engine(form, email, ats="lever"))
    assert email.attempts == 0
    assert result["state"] == "submission_unknown"


async def test_matched_code_is_handed_to_durable_wait_instead_of_cancelled(service):
    _, run, rule = await configured(service)
    request = service.mail.begin(run["id"], rule, "alex@example.test", "code")
    with service.db.session() as s:
        s.get(MailChallenge, request["id"]).state = "matched"
    result = {
        "state": "submission_unknown",
        "reason": "Submission attempted, but no explicit receipt was captured",
    }
    owner = SimpleNamespace(result=result, armed=True)
    email = object.__new__(EmailBrowser)
    email.engine, email.mail, email.challenge_id, email.deferred = owner, service.mail, request["id"], False

    email.close()

    with service.db.session() as s:
        assert s.get(MailChallenge, request["id"]).state == "matched"
    assert result["state"] == "submission_unknown"
    assert result["waiting_kind"] == "email"
    assert result["wait_handle"]["challenge_id"] == request["id"]


async def test_pending_request_without_a_code_is_still_cancelled(service):
    _, run, rule = await configured(service)
    request = service.mail.begin(run["id"], rule, "alex@example.test", "code")
    result = {
        "state": "submission_unknown",
        "reason": "Submission attempted, but no explicit receipt was captured",
    }
    email = object.__new__(EmailBrowser)
    email.engine, email.mail, email.challenge_id, email.deferred = (
        SimpleNamespace(result=result, armed=True),
        service.mail,
        request["id"],
        False,
    )

    email.close()

    with service.db.session() as s:
        assert s.get(MailChallenge, request["id"]).state == "cancelled"
    assert "waiting_kind" not in result


async def test_pre_armed_request_is_extended_to_its_durable_window_before_the_commit_is_polled(service):
    _, run, rule = await configured(service)
    request = service.mail.begin(run["id"], rule, "alex@example.test", "code")
    email = object.__new__(EmailBrowser)
    email.engine, email.mail, email.challenge_id, email.deferred = (
        SimpleNamespace(result=None, armed=True),
        service.mail,
        request["id"],
        False,
    )

    email.extend_pre_armed_request()

    with service.db.session() as s:
        row = s.get(MailChallenge, request["id"])
        assert row.state == "pending"
        assert row.expires == pytest.approx(row.since + service.config.mail_durable_wait_seconds)
