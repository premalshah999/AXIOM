"""A run released by an emailed sign-in link opens that link first (ClearCompany, Allen Institute, 2026-10-30).

The employer signs candidates in by an emailed link. The run that pressed "Next" parked, the link matched 16 s later and
released a new run, whose own "Next" came back with no "check your email" page (a second link inside a minute). Nothing
recognised the matched link, the model took the password route, and the run ended on a sign-in form that never changed:
"The employer returned its sign-in page without accepting or refusing the saved login", run after run."""

from collections import Counter
from types import SimpleNamespace

import pytest

from jobpilot.adapters import ICIMS, Oracle, Workday
from jobpilot.adapters.base import Adapter, Step
from jobpilot.db import Job, MailChallenge, Run
from jobpilot.recovery import repeats_unchanged

OBS = {"text": "Apply for Example Analyst", "fields": [], "controls": [], "headings": [], "url": "https://jobs.example.test/1"}
UNCHANGED = (
    "The employer returned its sign-in page without accepting or refusing the saved login; the existing account was "
    "kept and no new account was created"
)


def seed(service, token=None, state="matched", kind="auto", same_job=True, expires=1e12):
    """A job whose first run parked on a link wait that has matched; returns the handle the next run carries."""
    with service.db.exclusive() as s:
        s.add(Job(id="job1", identity="job1", url="https://jobs.example.test/1", ats="custom", company="ExampleSoft",
                  title="Analyst", status="waiting_email"))
        s.add(Job(id="job2", identity="job2", url="https://jobs.example.test/2", ats="custom", company="ExampleSoft",
                  title="Engineer", status="ready"))
        s.add(Run(id="source", job_id="job1" if same_job else "job2", mode="submit", state="waiting_email", packet={}))
        s.add(MailChallenge(
            id="c1", run_id="source", rule_id="r", mailbox_id="m", recipient="alex@example.test", kind=kind,
            state=state, since=0.0, expires=expires,
            payload=service.mail.vault.seal(token or {"kind": "link", "value": "https://jobs.example.test/signin"}),
        ))
    return {"source_run_id": "source", "challenge_id": "c1"}


def adapter(service, handle, cls=Adapter):
    value = cls.__new__(cls)
    value.service, value.attempts, value.committed = service, Counter(), False
    value.job = {"id": "job1", "ats": "custom", "url": "https://jobs.example.test/1"}
    value.run_record = {"packet": {"email_wait_resume": handle} if handle else {}}
    return value


def test_a_matched_sign_in_link_is_opened_before_the_page_is_read(service):
    value = adapter(service, seed(service))
    assert value.matched_link_waiting()
    assert value.classify_page(OBS) == Step.VERIFY_LINK  # on_verify_link: wait_email("link"), open_link, carry on
    assert callable(getattr(value, "on_" + Step.VERIFY_LINK.value))


def test_only_a_ready_link_of_this_job_counts(service):
    handle = seed(service)
    assert not adapter(service, None).matched_link_waiting()  # an ordinary run
    assert not adapter(service, {"challenge_id": "c1"}).matched_link_waiting()  # no run to prove the job
    spent = adapter(service, handle)
    spent.attempts["verify_link"] = 1  # already tried this run: never loops
    assert not spent.matched_link_waiting()
    foreign = adapter(service, handle)
    foreign.job = {"id": "job2", "ats": "custom", "url": "https://jobs.example.test/2"}
    assert not foreign.matched_link_waiting()  # the wait belongs to another job's run
    committed = adapter(service, handle)
    committed.committed = True  # after the final submit the page is read as it is
    assert committed.classify_page(OBS) != Step.VERIFY_LINK


@pytest.mark.parametrize(
    "token, state, kind, expires",
    [
        ({"kind": "code", "value": "000000"}, "matched", "auto", 1e12),  # a code is typed by the code step
        (None, "pending", "auto", 1e12),  # nothing arrived yet
        (None, "consumed", "auto", 1e12),
        (None, "matched", "code", 1e12),
        (None, "matched", "password_reset", 1e12),  # a reset has its own continuation
        (None, "matched", "auto", 1.0),  # past its window
    ],
)
def test_anything_else_is_left_to_the_ordinary_flow(service, token, state, kind, expires):
    value = adapter(service, seed(service, token, state, kind, expires=expires))
    assert not value.matched_link_waiting()
    assert value.classify_page(OBS) != Step.VERIFY_LINK


@pytest.mark.parametrize("cls", [Workday, ICIMS])
def test_platforms_with_their_own_continuation_keep_it(service, cls):
    assert cls.link_resume is False and Adapter.link_resume is True


def test_oracle_opens_a_matched_link_first(service):
    """A new install parked on Oracle's first link, signed in again on waking and created a second account request. That
    mailed a new link, which voided the one it then opened: 15 emails in two minutes, and nothing verified."""
    assert Oracle.link_resume is True
    assert adapter(service, seed(service), Oracle).classify_page(OBS) == Step.VERIFY_LINK


def test_oracle_leaves_a_matched_code_to_its_code_step(service):
    value = adapter(service, seed(service, {"kind": "code", "value": "000000"}), Oracle)
    assert value.classify_page(OBS) != Step.VERIFY_LINK


def link_visit(service, monkeypatch, text):
    """on_verify_link opening a link onto a page that says `text`; returns the adapter and what it recorded."""
    value = adapter(service, None)
    seen = SimpleNamespace(mail=[], marked=[], returned=0)

    async def ready(kind):
        return {"kind": "link", "value": "https://jobs.example.test/verify?token=t"}

    async def visit(url):
        return None

    async def scan():
        return {**OBS, "text": text, "fields": [{"id": "f", "type": "text", "label": "Name"}]}

    async def settle(timeout=0):
        return None

    async def back():
        seen.returned += 1

    monkeypatch.setattr(Adapter, "mail_rule_known", True)
    value.inbox, value.wait_email, value.open_link = SimpleNamespace(configured=True), ready, visit
    value.e = SimpleNamespace(form=SimpleNamespace(scan=scan, settle=settle, page=SimpleNamespace(url=OBS["url"])))
    value.finish_mail = lambda state, reason: seen.mail.append(state)
    value.accounts = SimpleNamespace(mark=lambda url, state: seen.marked.append(state))
    value.emit = lambda *a, **k: None
    value.return_to_application = back
    value.classify = lambda obs: Step.FORM
    return value, seen


@pytest.mark.parametrize(
    "text",
    [
        "This link has expired. Please ask for a new link.",
        "Your verification link is no longer valid",
        "That link has already been used.",
        "Invalid link",
    ],
)
async def test_a_refused_link_is_not_recorded_as_accepted(service, monkeypatch, text):
    value, seen = link_visit(service, monkeypatch, text)
    assert await value.on_verify_link(OBS) is None
    assert seen.mail == ["failed"] and not seen.marked and seen.returned == 1


@pytest.mark.parametrize("text", ["Your email has been verified. My Information", "Links to our benefits and privacy policy"])
async def test_an_accepted_link_signs_the_account_in(service, monkeypatch, text):
    value, seen = link_visit(service, monkeypatch, text)
    await value.on_verify_link(OBS)
    assert seen.mail == ["verified"] and seen.marked == ["authenticated"]


def test_a_sign_in_form_that_came_back_unchanged_is_not_retried_on_the_same_build():
    rows = [(UNCHANGED, "build-1"), (UNCHANGED, "build-1")]
    assert repeats_unchanged(rows)  # the same failing step on the same engine: wait for a new build
    assert not repeats_unchanged([(UNCHANGED, "build-1"), (UNCHANGED, "build-2")])  # a new build tries again
    assert not repeats_unchanged([("Waiting for the employer verification email", "build-1"), (UNCHANGED, "build-1")])


async def test_a_matched_link_that_cannot_be_claimed_hands_the_page_back_instead_of_ending_the_run(service, monkeypatch):
    value = adapter(service, seed(service))
    value.inbox = SimpleNamespace(configured=True)
    monkeypatch.setattr(Adapter, "mail_rule_known", True)

    async def nothing(kind):
        return None  # the wait changed hands, or its window ended

    value.wait_email = nothing
    assert await value.on_verify_link(OBS) == {"fallback": True, "reason": "The matched sign-in link could not be claimed"}
    ordinary = adapter(service, None)
    ordinary.inbox, ordinary.wait_email = value.inbox, nothing
    ordinary.mail_problem = lambda what: "did not arrive"
    ordinary.defer = lambda reason, *a, **k: {"state": "needs_review", "reason": reason}
    assert (await ordinary.on_verify_link(OBS))["state"] == "needs_review"  # a page that says "check your email" still defers
