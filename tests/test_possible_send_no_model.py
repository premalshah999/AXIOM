"""A send with nothing armed, then nothing able to read the page: the run ends where no retry can send it again.

A step's Continue can be the employer's real submit. The next page is the model's to read, and with the model down the run parked as
waiting_browser, which a retry starts again from the job's own Apply: a second application. The other half matters as much: nearly every
multi-step form writes at every step, and a draft that still shows its next step keeps its draft and its retry. Names are invented."""

import asyncio
import hashlib
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from gallery.scenarios import EMAIL_FIELD, NAME_FIELDS, POSTING, RESUME_FIELD, form, page
from gallery.site import Scenario, Site
from jobpilot.admission import QueueBlocked
from jobpilot.company_sites import allow
from jobpilot.db import Job, Resume, Run, Setting
from jobpilot.discovery import add_job
from jobpilot.schemas import JobInput
from test_gallery import RUN_SECONDS, blank_pdf
from test_wrong_posting_browser import sends_on_continue


async def run_then_retry(service, scenario, *, run_retry=True):
    """One run through the real engine, with no model to reach, then the very call `recovery.retry_due` makes.
    Returns (site, first run's row values, the retry: "blocked: <code>", its row values, or "allowed")."""
    config = service.config
    config.repair_agent_enabled = False
    config.mimo_api_key = "gallery-no-model"
    config.mimo_base_url = "http://127.0.0.1:9/v1"  # A closed port: the model is down.
    pdf = blank_pdf()
    (config.data_dir / "resumes").mkdir(exist_ok=True)
    (config.data_dir / "resumes" / "alex.pdf").write_bytes(pdf)
    with service.db.exclusive() as s:
        resume = Resume(name="Alex", filename="alex.pdf", sha256=hashlib.sha256(pdf).hexdigest(), text="Alex Example, software engineer.")
        s.add(resume)
        s.flush()
        resume_id = resume.id
    service.db.set_setting("profile", {"name": "Alex Example", "email": "alex@example.test", "autonomous": True,
                                       "accept_all_application_terms": scenario.terms,
                                       "facts": {"first_name": "Alex", "last_name": "Example", **scenario.facts}, **scenario.profile})
    service.db.set_setting("control", {"paused": False, "auto_submit": True})

    async def dispatch(_run_id):
        pass

    service.dispatch = dispatch
    site = Site(scenario.pages)

    def row(run_id):
        with service.db.session() as s:
            found = s.get(Run, run_id)
            return {"id": found.id, "state": found.state, "reason": found.reason, "receipt": found.receipt, "packet": found.packet}

    job, _ = add_job(service.db, JobInput(url=site.base + scenario.start, title=scenario.title, company=scenario.company,
                                          location="Remote", description="A synthetic posting."))
    with service.db.exclusive() as s:
        s.get(Job, job["id"]).status = "ready"
    allow(service.db, [job["id"]])
    try:
        run = await service.queue(job["id"], "submit", resume_id, owner_pick=True)
        await asyncio.wait_for(service.execute(run["id"]), RUN_SECONDS)
        first = row(run["id"])
        with service.db.exclusive() as s:  # The model is back: a parked outage is what `retry_due` then starts again.
            for setting in s.scalars(select(Setting).where(Setting.key.like("provider_health%"))):
                s.delete(setting)
        parked = service.db.get_setting("draft_recovery:" + job["id"], {})
        if parked:  # And its wait has passed.
            service.db.set_setting("draft_recovery:" + job["id"], {**parked, "not_before": "2000-01-01T00:00:00+00:00"})
        try:
            again = await service.queue(job["id"], "submit", retry=True, recovery_source_run=run["id"])
        except QueueBlocked as exc:
            return site, first, "blocked: " + exc.code
        if not run_retry:
            return site, first, "allowed"
        await asyncio.wait_for(service.execute(again["id"]), RUN_SECONDS)
        return site, first, row(again["id"])
    finally:
        site.close()


@pytest.mark.browser
@pytest.mark.gallery
@pytest.mark.integration
async def test_a_send_then_no_model_on_a_page_that_says_nothing_is_never_sent_again(service):
    site, first, retry = await run_then_retry(service, sends_on_continue("We will be in touch."))
    why = f"{first['state']}: {first['reason']}\nretry: {retry if isinstance(retry, str) else retry['state']}\nreceived: {len(site.applications())}"
    assert len(site.applications()) == 1, why  # What a retry of the parked run really did before this fix: 2.
    assert first["state"] == "submission_unknown", why
    assert first["receipt"].get("type") == "possible_send", why
    assert retry == "blocked: submitted_or_uncertain", why
    assert not first["packet"].get("recovery_cause"), why


@pytest.mark.browser
@pytest.mark.gallery
@pytest.mark.integration
async def test_a_send_then_no_model_on_a_page_that_thanks_is_confirmed_from_that_page(service):
    site, first, retry = await run_then_retry(service, sends_on_continue("Thank you for applying! Your application has been received."))
    why = f"{first['state']}: {first['reason']}\nretry: {retry if isinstance(retry, str) else retry['state']}\nreceived: {len(site.applications())}"
    assert len(site.applications()) == 1, why
    assert first["state"] == "confirmed", why
    assert retry.startswith("blocked:"), why


TWO_STEPS = Scenario(
    "step_saves_then_no_model", "",
    {"/jobs/1": page("Software Engineer", POSTING + '<p><a class="apply" href="/apply/1">Apply for this job</a></p>'),
     "/apply/1": page("Apply", "<h1>Apply: Software Engineer</h1><p>Step 1 of 2</p>" + form(
         NAME_FIELDS, EMAIL_FIELD, RESUME_FIELD, '<p><button type="submit">Continue</button></p>', action="/apply/2")),
     "/apply/2": lambda request: page("Apply", "<h1>Apply: Software Engineer</h1><p>Step 2 of 2</p>" + form(
         '<label for="q">Describe the project you are most proud of.</label><textarea id="q" name="project" required></textarea>',
         '<p><button type="submit">Next</button></p>', action="/apply/3"))},
)  # fmt: skip


@pytest.mark.browser
@pytest.mark.gallery
@pytest.mark.integration
async def test_a_step_that_saved_then_no_model_keeps_its_draft_and_its_retry(service):
    """The closest case: the first step's Continue wrote to the employer, and the next step is on the screen waiting for the model."""
    site, first, retry = await run_then_retry(service, TWO_STEPS, run_retry=False)
    why = f"{first['state']}: {first['reason']}\nretry: {retry}\nposts: {[p for p, _ in site.posts]}"
    assert [path for path, _ in site.posts][:1] == ["/apply/2"], why  # A step really was written.
    assert site.applications() == [], why
    assert first["state"] in {"waiting_browser", "needs_review"}, why
    assert retry == "allowed", why


# ----- the rule, on every kind of page ------------------------------------------------------------------------------------------


def control(label):
    return {"id": label, "label": label}


def field(label, kind="text"):
    return {"id": label, "label": label, "type": kind}


def obs(fields=(), controls=(), text=""):
    return {"url": "https://careers.acme.test/x", "fields": [field(*f) if isinstance(f, tuple) else field(f) for f in fields],
            "controls": [control(c) for c in controls], "text": text, "headings": []}


@pytest.mark.parametrize(
    "shown",
    [
        obs(["First name", "Email"], ["Next"]),  # The next step.
        obs(["Project"], ["Save and continue"]),
        obs(["Project"], ["Proceed"]),
        obs(["Project"], ["Submit application"]),  # The same form back, with its errors.
        obs([], ["Edit", "Submit application"]),  # A review step.
        obs([], ["Back", "Review"]),
        obs([("Password", "password"), ("Email", "email")], ["Sign in"]),  # An account step: a form has yet to come.
        obs([("Password", "password"), ("Confirm password", "password")], ["Create account"]),
        obs([], ["Apply", "About us"]),  # A posting with its Apply: the run is before the form.
    ],
)
def test_a_page_still_asking_for_the_application_is_a_draft(shown):
    from jobpilot.posting_match import still_applying

    assert still_applying(shown)


@pytest.mark.parametrize(
    "shown",
    [
        obs(text="We will be in touch."),  # The receipt that says nothing.
        obs([], ["Data Analyst", "Credit Risk Associate"], "Thanks. Similar jobs"),
        obs(["Email"], ["Subscribe"], "Get job alerts"),  # A receipt with a job-alert box.
        obs([], ["Continue to our careers site"]),
        obs(["First name"], ["Next"], "Thank you for applying! Your application has been received."),  # A survey after the receipt.
        obs([("Password", "password")], ["Create account"], "Your application has been received."),
        {},
        None,  # A page that can't be read.
    ],
)
def test_a_page_asking_nothing_of_the_application_could_be_its_receipt(shown):
    from jobpilot.posting_match import still_applying

    assert not still_applying(shown)


def ending(result, *, sent=True, armed=False, shown=None, steer=False):
    from jobpilot.browser import BrowserEngine
    from jobpilot.posting_match import PostingWatch

    e = object.__new__(BrowserEngine)
    e.armed, e.result, e.job = armed, result, {"url": "https://careers.acme.test/jobs/1", "id": "job"}
    e.run_record = {"packet": {"steer": steer}}
    e.posting_watch = PostingWatch(e.job)
    e.posting_watch.sent_like = sent
    e.emit = lambda *a, **k: None

    async def scan():
        if shown is None:
            raise RuntimeError("Target page, context or browser has been closed")
        return shown

    async def proof():
        return None

    e.form = SimpleNamespace(scan=scan, proof=proof, page=SimpleNamespace(url="https://careers.acme.test/thanks"))
    return e


MODEL_DOWN = {"state": "waiting_browser", "reason": "The selected model provider is unavailable", "blocker_type": "site_transient",
              "recovery_cause": "provider_unavailable"}
RECEIPT_LIKE = obs(text="We will be in touch.")


@pytest.mark.parametrize(
    "result",
    [
        MODEL_DOWN,
        {"state": "waiting_browser", "reason": "Model budget is exhausted; draft retained", "blocker_type": "site_transient", "recovery_cause": ""},
        {"state": "waiting_browser", "reason": "The model request did not produce a usable action", "blocker_type": "model_output", "recovery_cause": "model_output"},
        {"state": "waiting_browser", "reason": "An operation exceeded its deadline", "recovery_cause": "operation_timeout"},
        {"state": "timed_out", "reason": "Application exceeded its budget", "recovery_cause": "operation_timeout"},
        {"state": "timed_out", "reason": "The browser tab closed unexpectedly", "blocker_type": "site_transient", "recovery_cause": "browser_unavailable"},
        {"state": "waiting_browser", "reason": "Agent ended without verified completion", "recovery_cause": "navigation_stalled"},
        {"state": "failed", "reason": "Worker error"},
    ],
)
async def test_every_end_a_retry_could_restart_settles_as_a_possible_send(result):
    e = ending(dict(result), shown=RECEIPT_LIKE)
    await e.settle_possible_send()
    assert e.result["state"] == "submission_unknown"
    assert e.result["receipt"]["type"] == "possible_send"
    assert "recovery_cause" not in e.result and "isn't sent again" in e.result["reason"]


async def test_a_page_that_cannot_be_read_counts_as_a_possible_send():
    e = ending(dict(MODEL_DOWN), shown=None)
    await e.settle_possible_send()
    assert e.result["state"] == "submission_unknown"


async def test_a_tab_that_crashed_after_the_next_step_was_read_keeps_its_draft():
    """A crash mid-form is infrastructure: the run saw this write's next step, and the retry continues it."""
    crashed = {"state": "timed_out", "reason": "The browser tab closed unexpectedly", "blocker_type": "site_transient", "recovery_cause": "browser_unavailable"}
    e = ending(dict(crashed), shown=None)
    e.posting_watch.judge("https://careers.acme.test/apply/2", obs(["Project"], ["Next"]))
    await e.settle_possible_send()
    assert e.result == crashed


async def test_a_blank_tab_is_no_more_than_a_tab_that_crashed():
    e = ending(dict(MODEL_DOWN), shown=obs())
    await e.settle_possible_send()
    assert e.result["state"] == "submission_unknown"


def test_a_later_write_makes_its_page_unread_again():
    """Step 2 was read, and then its own Continue went out: that write's page is what counts."""
    from jobpilot.browser import BrowserEngine

    e = ending(dict(MODEL_DOWN), shown=None)
    e.posting_watch.judge("https://careers.acme.test/apply/2", obs(["Project"], ["Next"]))
    assert e.posting_watch.step_after_write
    e.real_write = lambda _request: True
    e.form.ledger, e.form.uploaded = {"a": 1}, False
    BrowserEngine.note_possible_send(e, SimpleNamespace(url="https://careers.acme.test/apply/3", post_data_buffer=b""), SimpleNamespace(status=200))
    assert e.posting_watch.sent_like and not e.posting_watch.step_after_write


async def test_a_run_ended_with_no_result_leaves_word_for_the_worker():
    """Its outer deadline cancelled it: the worker writes the run's end, and reads this."""
    e = ending(None, shown=RECEIPT_LIKE)
    await e.settle_possible_send()
    assert e.result is None and e.possible_send_end["state"] == "submission_unknown"


@pytest.mark.parametrize(
    "change",
    [
        {"sent": False},  # Nothing went out: the model outage is only that.
        {"armed": True},  # The commit's own handling says what happened.
        {"steer": True},  # The owner is at the wheel.
        {"shown": obs(["Project"], ["Next"])},  # The next step is on the screen.
    ],
)
async def test_an_outage_that_sent_nothing_keeps_its_draft(change):
    e = ending(dict(MODEL_DOWN), **{"shown": RECEIPT_LIKE, **change})
    await e.settle_possible_send()
    assert e.result == MODEL_DOWN


@pytest.mark.parametrize(
    "result",
    [
        {"state": "waiting_browser", "reason": "Waiting for the employer verification email", "blocker_type": "email_wait", "waiting_kind": "email"},
        {"state": "waiting_browser", "reason": "Security check", "blocker_type": "captcha", "recovery_cause": "security_challenge"},
        {"state": "waiting_browser", "reason": "The employer rejected the credentials", "blocker_type": "account", "recoverable": False},
        {"state": "waiting_browser", "reason": "The employer asked for a wait", "blocker_type": "site_transient", "recovery_cause": "reset_cooldown"},
        {"state": "needs_review", "reason": "Needs the applicant's answer", "blocker_type": "missing_fact"},
        {"state": "closed", "reason": "The posting is closed"},
        {"state": "stopped", "reason": "Stopped"},
        {"state": "confirmed", "reason": "Website confirmation captured"},
    ],
)
async def test_an_end_that_named_what_is_on_the_page_stands(result):
    e = ending(dict(result), shown=RECEIPT_LIKE)
    await e.settle_possible_send()
    assert e.result == result


async def test_the_employers_thanks_confirms_rather_than_leaving_it_unknown():
    e = ending(dict(MODEL_DOWN), shown=obs(text="Thank you for applying!"))

    async def proof():
        return {"type": "explicit_confirmation_page", "website_confirmed": True}

    e.form.proof = proof
    await e.settle_possible_send()
    assert e.result["state"] == "confirmed" and e.result["receipt"]["website_confirmed"] and "recovery_cause" not in e.result


def test_the_resubmit_policy_never_resends_a_possible_send(service):
    """Off by default, on by the owner's choice for a commit whose outcome is unknown: nothing read this page, so nothing clears it."""
    from datetime import UTC, datetime, timedelta

    from jobpilot.admission import unknown_resubmit_allowed

    now = datetime.now(UTC)
    service.db.set_setting("unknown_resubmit_policy", {"enabled": True})
    run = SimpleNamespace(state="submission_unknown", receipt={"type": "possible_send"}, job_id="job", finished_at=(now - timedelta(hours=2)).isoformat(),
                          created_at=(now - timedelta(hours=2)).isoformat())
    plain = SimpleNamespace(**{**run.__dict__, "receipt": {}})
    with service.db.session() as s:
        assert unknown_resubmit_allowed(s, [plain], now, checked=False)
        assert not unknown_resubmit_allowed(s, [run], now, checked=False)


async def test_a_run_cancelled_at_the_outer_deadline_ends_as_its_last_look_said(service, monkeypatch):
    """The worker writes this end itself: timed_out, which a retry starts again, unless the run's last look found a possible send."""
    import jobpilot.pipeline_comparison as pipeline

    service.dispatch = None
    service.db.set_setting("profile", {"name": "Alex Example", "email": "alex@example.test", "autonomous": True, "facts": {}})
    service.db.set_setting("control", {"paused": False, "auto_submit": True})
    job, _ = add_job(service.db, JobInput(url="https://careers.example.test/jobs/1", title="Software Engineer", company="ExampleSoft",
                                          location="Remote", description="A synthetic posting."))
    with service.db.exclusive() as s:
        s.get(Job, job["id"]).status = "queued"
        run = Run(job_id=job["id"], state="queued", mode="submit", packet={})
        s.add(run)
        s.flush()
        run_id = run.id

    class Engine:
        result = None
        possible_send_end = {"state": "submission_unknown", "reason": "It isn't sent again", "blocker_type": "possible_send",
                             "receipt": {"type": "possible_send"}}

        async def run(self, _job, _run):
            raise TimeoutError  # What the outer deadline's cancel reaches the worker as.

    monkeypatch.setattr(pipeline, "engine_for", lambda _service, _run: Engine())
    await asyncio.wait_for(service.execute(run_id), 25)
    with service.db.session() as s:
        ended = s.get(Run, run_id)
        assert ended.state == "submission_unknown" and ended.receipt.get("type") == "possible_send", ended.reason
        assert not ended.packet.get("recovery_cause")
