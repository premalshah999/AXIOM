"""After a commit click, a page that still shows field errors is a refused form, not an unknown submission.

Ashby (DailyPay fbfdc573): "Your form needs corrections" was already on the page before the commit,
so the identical-banner check ignored it for the whole 30 s window and the run ended submission_unknown.
Greenhouse: an inline phone error arrived with the refusal. Exits after a commit (mail wait, deadline,
control failure) take one bounded look at the page, and only real proof confirms.
"""

import asyncio
from types import SimpleNamespace

import pytest

import jobpilot.browser as browser
import jobpilot.widgets as widgets
from jobpilot.answers import Resolver
from jobpilot.browser import BrowserEngine
from jobpilot.forms import FormSession
from jobpilot.schemas import Profile

ASHBY_BANNER = {
    "type": "explicit_rejection_page",
    "category": "validation",
    "url": "https://jobs.ashbyhq.com/acme/1",
    "missing_fields": ["Base Salary Expectations"],
}
PHONE_ERROR = {
    "type": "explicit_validation_rejection",
    "category": "validation",
    "url": "https://job-boards.greenhouse.io/acme/jobs/1",
    "missing_fields": ["Phone"],
}
RECEIPT = {"url": "https://jobs.example.test/acme/1/confirmation", "type": "explicit_confirmation_page"}


class FastAsyncio:
    """The commit loop polls every half second for 30 s; the tests only need the polls."""

    def __getattr__(self, name):
        return getattr(asyncio, name)

    @staticmethod
    async def sleep(delay, *args, **kwargs):
        await asyncio.sleep(0)


WORKDAY_URL = "https://acme.wd1.myworkdayjobs.com/en-US/ext/job/x/Analyst_1"


async def serve(page, url, body):
    async def route(request):
        await request.fulfill(status=200, content_type="text/html", body=body)

    await page.context.route("**/*", route)
    await page.goto(url)


async def noop(*args, **kwargs):
    return None


class CommitForm:
    """A verified form at its single final submit. `shown` is what the page displays; the
    commit click replaces it with `after_click`, and proof appears only if `receipt` is set."""

    def __init__(self, shown, after_click, receipt=None):
        self.shown, self.after_click, self.receipt = shown, after_click, receipt
        self.clicks = 0
        self.committed = False
        self.repairs = 0

    async def verify(self):
        return {"ok": True, "resume_attached": True, "verified_fields": True, "problems": [], "pending": []}

    async def scan(self):
        return {"fields": [], "text": "", "dialogs": []}

    async def application_submit_controls(self):
        return [{"id": "submit-1", "label": "Submit application", "disabled": False, "section": ""}]

    async def settle(self, **kwargs):
        return None

    async def proof(self):
        return self.receipt if self.committed else None

    async def rejection(self):
        return self.shown

    async def repair_validation(self, rejection):
        self.repairs += 1
        return {"ok": False, "changed": False, "pending": []}

    def locator(self, control_id):
        form = self

        class Control:
            async def click(self, **kwargs):
                form.clicks += 1
                form.committed = True
                form.shown = form.after_click

            async def scroll_into_view_if_needed(self, **kwargs):
                return None

        return Control()


def engine_for(monkeypatch, form, ats="ashby"):
    monkeypatch.setattr(browser, "asyncio", FastAsyncio())
    monkeypatch.setattr(widgets, "ensure_uncovered", noop)
    engine = object.__new__(BrowserEngine)
    engine.form = form
    engine.armed = False
    engine.result = None
    engine.job = {"demo": False, "ats": ats, "url": "https://jobs.example.test/acme/1"}
    engine.run_record = {"mode": "submit", "packet": {"resume_sha": "resume-sha"}}
    engine.page = SimpleNamespace(frames=[])
    engine.events = []
    engine.emit = lambda kind, message, data=None: engine.events.append(kind)
    engine.email_verification = SimpleNamespace(
        prepare_submission=noop, state=lambda observation: (False, None)
    )
    engine.quality_gate = noop
    engine.browser_challenge = noop
    return engine


async def test_banner_that_predates_the_commit_and_stays_is_a_validation_refusal(monkeypatch):
    form = CommitForm(shown=ASHBY_BANNER, after_click=ASHBY_BANNER)
    engine = engine_for(monkeypatch, form)

    result = await engine.finish(reserved=True)

    assert result["state"] == "waiting_browser", result
    assert result["blocker_type"] == "validation"
    assert result["recovery_cause"] == "validation_controls"
    assert result["receipt"]["missing_fields"] == ["Base Salary Expectations"]
    assert engine.armed is False
    assert form.clicks == 1, "the refused commit is never retried from finish()"


async def test_inline_error_arriving_with_the_refusal_goes_to_repair(monkeypatch):
    form = CommitForm(shown=None, after_click=PHONE_ERROR)
    engine = engine_for(monkeypatch, form, ats="greenhouse")

    result = await engine.finish(reserved=True)

    assert result["state"] == "waiting_browser", result
    assert result["blocker_type"] == "validation"
    assert form.repairs == 1
    assert form.clicks == 1
    assert engine.armed is False


async def test_error_that_disappears_after_the_commit_stays_unknown(monkeypatch):
    # No refusal is visible and no proof exists: the outcome is genuinely unknown.
    form = CommitForm(shown=ASHBY_BANNER, after_click=None)
    engine = engine_for(monkeypatch, form)

    result = await engine.finish(reserved=True)

    assert result["state"] == "submission_unknown", result
    assert form.clicks == 1


async def test_real_proof_after_the_commit_confirms_even_with_a_stale_banner(monkeypatch):
    form = CommitForm(shown=ASHBY_BANNER, after_click=ASHBY_BANNER, receipt=RECEIPT)
    engine = engine_for(monkeypatch, form)

    result = await engine.finish(reserved=True)

    assert result["state"] == "confirmed", result
    assert result["receipt"]["url"] == RECEIPT["url"]


async def test_rejection_gains_inline_errors_beside_a_banner_that_names_other_fields(page, service, tmp_path):
    resolver = Resolver(
        Profile(), service.db, service.config, run_id="run-1", job={"id": "job-1", "ats": "ashby"}
    )
    form = FormSession(page, resolver, tmp_path / "resume.pdf", lambda *a: None)
    banner = (
        "<div>Your form needs corrections. Missing entry for required field: Base Salary Expectations</div>"
    )
    await page.set_content(
        f"<html><body>{banner}<form><label>Phone<input name=phone></label><button>Submit</button></form></body></html>"
    )
    before = await form.rejection()
    await page.set_content(
        f"<html><body>{banner}<form><label>Phone<input name=phone aria-invalid=true aria-describedby=phone-error>"
        "</label><div id=phone-error>Phone number is too short</div><button>Submit</button></form></body></html>"
    )
    after = await form.rejection()

    assert before["missing_fields"] == ["Base Salary Expectations"]
    assert after != before, "a new inline error must not be hidden by the identical banner"
    assert after["missing_fields"] == ["Base Salary Expectations", "Phone"]


@pytest.mark.parametrize(
    "body",
    [
        "<div role=dialog aria-modal=true><h2>Application Submitted</h2><p>Your application was submitted successfully.</p></div>",
        "<div role=dialog aria-modal=true><button>Close</button><h2>Application Submitted</h2>"
        "<p>Application Received for Analyst</p></div><ul><li>Analyst - Application Received</li></ul>",
        # Chewy (2026-10-13): no "submitted" wording at all.
        "<div role=dialog aria-modal=true><h2>Congratulations!</h2><p>We have received your application.</p></div>",
        # Adobe (2026-10-17): verb before noun, and the dialog holds a create-account form.
        "<div role=dialog aria-modal=true><button>Close</button><h2>Congratulations!</h2><p>You've successfully "
        "submitted your application. We'll be in touch if your skills are a match.</p><label>Email Address"
        "<input type=email></label><label>Password<input type=password></label></div>",
    ],
)
async def test_workday_submitted_dialog_is_proof_after_a_commit_exit(page, service, tmp_path, body):
    resolver = Resolver(
        Profile(),
        service.db,
        service.config,
        run_id="run-1",
        job={"id": "job-1", "ats": "workday", "url": WORKDAY_URL},
    )
    form = FormSession(page, resolver, tmp_path / "resume.pdf", lambda *a: None)
    await serve(
        page, WORKDAY_URL, f"<html><head><title>My Applications</title></head><body>{body}</body></html>"
    )
    # proof() already recognizes the modal (Workday's dialog branch), with or without a commit exit.
    receipt = await form.proof()
    assert receipt is not None
    assert receipt["evidence_selector"] == "workday_application_submitted_dialog"

    engine = object.__new__(BrowserEngine)
    engine.form = form
    engine.armed = True
    engine.run_record = {"packet": {"resume_sha": "resume-sha"}}
    result = await engine.confirm_post_commit()

    assert result is not None
    assert result["state"] == "confirmed"
    assert result["receipt"]["evidence_selector"] == "workday_application_submitted_dialog"
    assert result["receipt"]["resume_sha256"] == "resume-sha"


async def test_post_commit_exit_does_not_confirm_the_page_that_was_already_showing(page, service, tmp_path):
    resolver = Resolver(
        Profile(),
        service.db,
        service.config,
        run_id="run-1",
        job={"id": "job-1", "ats": "workday", "url": WORKDAY_URL},
    )
    form = FormSession(page, resolver, tmp_path / "resume.pdf", lambda *a: None)
    await serve(
        page,
        WORKDAY_URL,
        "<html><body><div role=dialog aria-modal=true><h2>Application Submitted</h2>"
        "<p>Your application was submitted successfully.</p></div></body></html>",
    )
    engine = object.__new__(BrowserEngine)
    engine.form = form
    engine.armed = True
    engine.run_record = {"packet": {"resume_sha": "resume-sha"}}
    engine.pre_commit_proof = await form.proof()

    assert await engine.confirm_post_commit() is None

    engine.armed = False
    engine.pre_commit_proof = None
    assert await engine.confirm_post_commit() is None, "an exit before any commit never confirms"


SECURITY = {"type": "explicit_rejection_page", "category": "security", "url": "https://jobs.lever.co/acme/1/apply"}


@pytest.mark.parametrize("outcome", ["refused", "accepted"])
async def test_a_post_submit_puzzle_that_outlasts_the_commit_reads_the_page_again(monkeypatch, outcome):
    # Lever 2026-10-18: while the post-submit hCaptcha puzzle was answered, the site posted the form and answered.
    # The solver's "not solved" must not turn the page's own refusal (or receipt) into submission_unknown.
    form = CommitForm(shown=None, after_click=None)
    engine = engine_for(monkeypatch, form, ats="lever")

    async def puzzle_while_the_site_answers(pre_submit=False, **kwargs):
        if pre_submit:
            return None
        if outcome == "refused":
            form.shown = SECURITY
        else:
            form.receipt = RECEIPT
        return {"state": "waiting_browser", "reason": "The hCaptcha puzzle was not solved in this browser"}

    engine.browser_challenge = puzzle_while_the_site_answers
    result = await engine.finish(reserved=True)
    if outcome == "refused":
        assert result["state"] == "waiting_browser" and result["recovery_cause"] == "site_temporary", result
    else:
        assert result["state"] == "confirmed", result
    assert form.clicks == 1



async def test_a_workday_confirmation_after_thirty_seconds_still_counts(monkeypatch):
    # Chewy 2026-10-17: the "Congratulations!" dialog was still fading in when the 30 s window closed.
    from jobpilot.browser import POST_SUBMIT_POLLS

    for ats, confirmed in (("workday", True), ("oracle", True), ("ashby", False)):
        form = CommitForm(shown=None, after_click=None)
        engine = engine_for(monkeypatch, form, ats=ats)
        calls = {"n": 0}

        async def late_proof():
            calls["n"] += 1
            return RECEIPT if form.committed and calls["n"] > POST_SUBMIT_POLLS + 20 else None

        form.proof = late_proof
        result = await engine.finish(reserved=True)
        assert (result["state"] == "confirmed") is confirmed, (ats, result)


@pytest.mark.parametrize(
    "category,state,cause",
    [("temporary", "waiting_browser", "site_temporary"), ("closed", "closed", None)],
)
async def test_smartrecruiters_refusals_end_the_commit_honestly(monkeypatch, category, state, cause):
    refusal = {"type": "explicit_rejection_page", "category": category, "url": "https://jobs.smartrecruiters.com/x"}
    form = CommitForm(shown=None, after_click=refusal)
    engine = engine_for(monkeypatch, form, ats="smartrecruiters")
    result = await engine.finish(reserved=True)
    assert result["state"] == state and result.get("recovery_cause") == cause, result
    assert engine.armed is False and form.clicks == 1
