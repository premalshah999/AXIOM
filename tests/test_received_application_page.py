"""Humana (Phenom front end, Workday back end), 2026-11-01: the review step's "Continue" is the application's submit and its reCAPTCHA token's
callback presses it. The run delivered that token as an ordinary step's, posted the application unarmed, timed out clicking the button the
send had disabled, and later reopened the site's "One Last Step! ... creating your Candidate Home Account" page and spent its account window
signing in to Workday. That page has none of the words the resubmit policy reads, so a sent-unknown ending alone would have sent it again."""

from types import SimpleNamespace

import pytest

from jobpilot.adapters.base import Adapter
from jobpilot.autopilot import resubmit_unknown, unknown_candidates
from jobpilot.browser import COMMIT_TOKEN_SECONDS, RECEIVED_REASON, BrowserEngine, received_application_page
from jobpilot.db import Job, Run
from jobpilot.forms import final_by_marking

JOB = "https://careers.acme-health.test/us/en/job/ACMACMUSR434028EXTERNALENUS/AI-Engineer?_ccid=1"
THANKS = "https://careers.acme-health.test/us/en/applythankyou?status=success&jobSeqNo=ACMACMUSR434028EXTERNALENUS&jobId=R-434028"
ONE_LAST_STEP = (
    "h1: One Last Step!\nh2: AI Engineer\np: There's just one last step, which is creating your Candidate Home Account. This account allows "
    "you to view your application status and access application related tasks throughout the process.\nbutton: Continue\n"
    "p: We're excited you're considering a career with us.\na: Submit a request"
)
REVIEW = "https://careers.acme-health.test/us/en/apply?jobSeqNo=ACMACMUSR434028EXTERNALENUS&step=6&stepname=applicationReview"
SUBMIT = {"id": "c", "label": "Continue", "atm_id": "submit-button", "automation_id": "", "aria_label": "Continue", "type": "submit"}


def test_the_employers_success_page_for_this_posting_is_a_receipt():
    assert received_application_page(THANKS, JOB)
    # A posting addressed by its requisition id rather than its long sequence number.
    assert received_application_page(THANKS, "https://careers.acme-health.test/global/en/job/R-434028/AI-Engineer")


@pytest.mark.parametrize(
    "url",
    [
        "https://careers.acme-health.test/us/en/applythankyou?status=failure&jobSeqNo=ACMACMUSR434028EXTERNALENUS",
        "https://careers.acme-health.test/us/en/applythankyou?status=success&jobSeqNo=ACMOTHERJOB999999&jobId=R-999",  # another posting's
        "https://careers.acme-health.test/us/en/applythankyou?status=success",  # names no posting
        "https://careers.acme-health.test/us/en/apply?status=success&jobSeqNo=ACMACMUSR434028EXTERNALENUS",  # not a thank-you path
        "https://careers.acme-health.test/us/en/job/ACMACMUSR434028EXTERNALENUS/AI-Engineer",
        # Part of a longer id is not this posting's.
        "https://careers.acme-health.test/us/en/applythankyou?status=success&jobSeqNo=434028",
    ],
)
def test_nothing_else_is_taken_for_a_receipt(url):
    assert not received_application_page(url, JOB)


def _ended_on_the_receipt_page(service, n=1, minutes_ago=120):
    from datetime import UTC, datetime, timedelta

    at = (datetime.now(UTC) - timedelta(minutes=minutes_ago)).isoformat()
    with service.db.exclusive() as s:
        s.add(Job(id=f"h{n}", identity=f"h{n}", url=JOB, ats="custom", company="Acme Health", status="submission_unknown"))
        s.add(Run(id=f"hr{n}", job_id=f"h{n}", mode="submit", state="submission_unknown", created_at=at, finished_at=at, started_at=at,
                  reason=RECEIVED_REASON))
    folder = service.config.data_dir / "runs" / f"hr{n}"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "final_outline.txt").write_text(ONE_LAST_STEP)
    return SimpleNamespace(db=service.db, job={"id": f"h{n}", "url": JOB}, run_id=f"hr{n}", emit=lambda *a, **k: None)


async def test_the_receipt_page_cannot_lead_to_a_resend_under_the_installations_resubmit_policy(service, monkeypatch):
    """An installation that enabled the policy would resubmit this one: no receipt mail, a final page with no confirmation wording."""
    service.db.set_setting("unknown_resubmit_policy", {"enabled": True, "max_resubmits": 6})
    engine = _ended_on_the_receipt_page(service)
    queued = []

    async def queue(job_id, mode, *args, **kwargs):
        queued.append(job_id)
        return {"id": "again"}

    async def searched(mail, run_id):
        return False  # The mailbox holds nothing: the receipt mail is not what protects it.

    monkeypatch.setattr(service, "queue", queue)
    monkeypatch.setattr("jobpilot.receipts.employer_acknowledged", searched)
    assert unknown_candidates(service.db, False) == [("h1", "hr1")]  # Without the record it is a candidate, which is the bug.
    assert BrowserEngine.received_application(engine)["state"] == "submission_unknown"
    assert unknown_candidates(service.db, False) == []
    assert await resubmit_unknown(service, 5) == 0 and queued == []


async def test_no_queue_takes_a_job_settled_by_the_receipt_page(service):
    from jobpilot.admission import QueueBlocked

    service.db.set_setting("unknown_resubmit_policy", {"enabled": True, "max_resubmits": 6})
    BrowserEngine.received_application(_ended_on_the_receipt_page(service))
    with pytest.raises(QueueBlocked) as refused:
        await service.queue("h1", "submit")
    assert refused.value.as_dict()["code"] == "submitted_or_uncertain"


@pytest.mark.parametrize("settled,outcome", [(True, "unchanged"), (False, "not_submitted")])
async def test_the_repair_agent_cannot_reopen_it_as_not_submitted(service, monkeypatch, settled, outcome):
    """A model's reading of the page ("Submit a request" in its footer, no thanks in its words) never undoes what the address settled."""
    from jobpilot.repair_agent import RepairPlan, repair_run

    engine = _ended_on_the_receipt_page(service)
    if settled:
        BrowserEngine.received_application(engine)
    service.db.set_setting("profile", {"first_name": "Alex", "last_name": "Rivera"})

    async def plan(*args, **kwargs):
        return RepairPlan(category="control", diagnosis="The page shows no acknowledgement", confidence=0.9, submission="not_submitted")

    reconciled = []
    monkeypatch.setattr("jobpilot.models.structured", plan)
    monkeypatch.setattr(service, "reconcile", lambda *a, **k: reconciled.append(a), raising=False)
    summary = await repair_run(service, "hr1")
    assert summary["outcome"] == outcome and bool(reconciled) is not settled


async def test_a_run_that_ended_any_other_way_on_the_receipt_page_says_it_was_received(service):
    """Humana's click timed out on the button the send had disabled, reported as "application tab retained for recovery"."""
    engine = _ended_on_the_receipt_page(service, 2)
    result = {"state": "waiting_browser", "reason": "Browser control failed at widgets.py:1310; application tab retained for recovery",
              "blocker_type": "control_failure", "recovery_cause": "control_timeout"}
    engine.result, engine.page = result, SimpleNamespace(url=THANKS)
    engine.received_application = lambda: BrowserEngine.received_application(engine)
    BrowserEngine.settle_on_receipt_page(engine)
    assert result["state"] == "submission_unknown" and result["reason"] == RECEIVED_REASON
    assert "recovery_cause" not in result and "blocker_type" not in result
    assert service.db.get_setting("unknown_triage:h2")["verdict"] == "submitted"


@pytest.mark.parametrize("ended", ["confirmed", "already_applied", "closed"])
async def test_a_settled_outcome_is_left_as_it_is(service, ended):
    engine = _ended_on_the_receipt_page(service, 3)
    engine.result, engine.page = {"state": ended, "reason": "x"}, SimpleNamespace(url=THANKS)
    engine.received_application = lambda: BrowserEngine.received_application(engine)
    BrowserEngine.settle_on_receipt_page(engine)
    assert engine.result["state"] == ended and service.db.get_setting("unknown_triage:h3", None) is None


class Scanned(Adapter):
    final = __import__("jobpilot.forms", fromlist=["FINAL"]).FINAL
    next_ids = ()

    def __init__(self):
        pass


def test_the_review_steps_marked_continue_is_the_submit_and_never_a_step():
    adapter = Scanned()
    obs = {"url": REVIEW, "controls": [SUBMIT, {"id": "p", "label": "Previous Step", "atm_id": "", "automation_id": "", "aria_label": ""}]}
    assert final_by_marking(SUBMIT, REVIEW)
    assert adapter.find_final(obs)["id"] == "c" and adapter.find_next(obs) is None


@pytest.mark.parametrize(
    "control,url",
    [
        ({**SUBMIT, "atm_id": ""}, REVIEW),  # The earlier steps' id="next" "Next Step".
        (SUBMIT, REVIEW.replace("step=6&stepname=applicationReview", "step=2&stepname=workAndEducation")),
        (SUBMIT, JOB),
    ],
)
def test_nothing_else_reads_as_the_marked_submit(control, url):
    adapter = Scanned()
    obs = {"url": url, "controls": [control]}
    assert not final_by_marking(control, url)
    assert adapter.find_final(obs) is None and adapter.find_next(obs)["id"] == "c"


class Locator:
    def __init__(self, disabled_after):
        self.calls, self.disabled_after = 0, disabled_after

    async def is_disabled(self, timeout=0):
        self.calls += 1
        return self.disabled_after is not None and self.calls > self.disabled_after


def token_engine(*, delivers=True, locator=None, unsolved=None):
    delivered = {}
    page = SimpleNamespace(url=REVIEW)
    engine = SimpleNamespace(
        captcha=SimpleNamespace(token_delivered_at=delivered),
        form=SimpleNamespace(page=page, locator=lambda _: locator or Locator(0)),
        tokens=0,
    )

    async def browser_challenge():
        engine.tokens += 1
        if delivers and not unsolved:
            delivered["sig"] = engine.tokens + 1000.0
        return unsolved

    engine.browser_challenge = browser_challenge
    return engine


async def test_the_token_that_starts_the_send_is_not_followed_by_a_click_or_a_second_token():
    engine = token_engine()
    assert await BrowserEngine.commit_by_token(engine, SUBMIT) is True and engine.tokens == 1
    assert await BrowserEngine.commit_by_token(engine, SUBMIT) is True and engine.tokens == 1


async def test_an_unsolved_check_is_reported_with_nothing_sent():
    engine = token_engine(unsolved={"state": "waiting_browser", "reason": "unsolved"})
    assert (await BrowserEngine.commit_by_token(engine, SUBMIT))["reason"] == "unsolved"
    assert not getattr(engine, "commit_token_sent", False)


async def test_a_step_that_asks_no_token_is_clicked():
    assert await BrowserEngine.commit_by_token(token_engine(delivers=False), SUBMIT) is None


async def test_a_token_that_moves_nothing_leaves_the_button_to_be_pressed(monkeypatch):
    monkeypatch.setattr("jobpilot.browser.COMMIT_TOKEN_SECONDS", 0.05)
    engine = token_engine(locator=Locator(None))
    assert await BrowserEngine.commit_by_token(engine, SUBMIT) is None and not engine.commit_token_sent


async def test_any_other_page_is_not_a_token_commit():
    engine = token_engine()
    assert await BrowserEngine.commit_by_token(engine, {**SUBMIT, "atm_id": ""}) is None and engine.tokens == 0


async def test_no_token_is_delivered_on_the_marked_review_step_outside_the_commit():
    calls = []

    async def scan():
        return {"controls": [SUBMIT]}

    async def wall():
        return None

    async def solve(page, **kwargs):
        calls.append(page)
        return False

    engine = BrowserEngine.__new__(BrowserEngine)
    engine.armed, engine.page = False, SimpleNamespace(url=REVIEW)
    engine.form = SimpleNamespace(page=engine.page, scan=scan)
    engine.captcha = SimpleNamespace(solve=solve)
    engine.platform_wall = wall
    assert await BrowserEngine.browser_challenge(engine) is None and calls == []
    assert COMMIT_TOKEN_SECONDS > 0
