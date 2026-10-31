"""No submit control after the commit is no control failure (fix-submit, 2026-10-31).

The click took effect: the page is read for an acknowledgement under the usual evidence rules, else the submission is
uncertain and nothing is clicked again. Two controls stay "not unique". Synthetic data only."""

import asyncio
from types import SimpleNamespace

import jobpilot.browser as browser
import jobpilot.widgets as widgets
from jobpilot.browser import BrowserEngine


class FastAsyncio:
    def __getattr__(self, name):
        return getattr(asyncio, name)

    @staticmethod
    async def sleep(delay, *args, **kwargs):
        await asyncio.sleep(0)


async def noop(*args, **kwargs):
    return None


class NoControlForm:
    """A verified form whose page shows `controls` submit controls, and a receipt only once `receipt` is set."""

    meta = {}

    def __init__(self, controls, receipt=None):
        self.controls, self.receipt, self.clicks = controls, receipt, 0

    async def verify(self):
        return {"ok": True, "resume_attached": True, "verified_fields": True, "problems": [], "pending": []}

    async def scan(self):
        return {"fields": [], "text": "", "dialogs": [], "controls": []}

    async def application_submit_controls(self):
        return [{"id": f"s{i}", "label": "Submit", "disabled": False, "section": ""} for i in range(self.controls)]

    async def settle(self, **kwargs):
        return None

    async def proof(self):
        return self.receipt

    async def rejection(self):
        return None

    def resume_fields(self):
        return []

    def locator(self, control_id):
        form = self

        class Control:
            async def click(self, **kwargs):
                form.clicks += 1

        return Control()


def engine_for(monkeypatch, form, armed):
    monkeypatch.setattr(browser, "asyncio", FastAsyncio())
    monkeypatch.setattr(widgets, "ensure_uncovered", noop)
    engine = object.__new__(BrowserEngine)
    engine.form, engine.armed, engine.result = form, armed, None
    engine.job = {"demo": False, "ats": "custom", "url": "https://jobs.example.test/acme/1"}
    engine.run_record = {"mode": "submit", "packet": {"resume_sha": "resume-sha"}}
    engine.page = SimpleNamespace(frames=[])
    engine.events = []
    engine.emit = lambda kind, message, data=None: engine.events.append(kind)
    engine.quality_gate = noop
    engine.browser_challenge = noop
    engine.pre_commit_proof = {"confirmation": "before"}
    return engine


async def test_no_submit_control_after_the_commit_is_an_uncertain_submission_never_a_control_failure(monkeypatch):
    form = NoControlForm(controls=0)
    result = await engine_for(monkeypatch, form, armed=True).finish()
    assert result["state"] == "submission_unknown" and "not unique" not in result["reason"]
    assert form.clicks == 0


async def test_no_submit_control_after_the_commit_confirms_on_a_new_acknowledgement(monkeypatch):
    form = NoControlForm(controls=0, receipt={"type": "explicit_confirmation_page", "confirmation": "Thank you"})
    result = await engine_for(monkeypatch, form, armed=True).finish()
    assert result["state"] == "confirmed" and result["receipt"]["confirmation"] == "Thank you"


async def test_the_acknowledgement_that_was_showing_before_the_commit_is_not_new(monkeypatch):
    form = NoControlForm(controls=0, receipt={"confirmation": "before"})
    result = await engine_for(monkeypatch, form, armed=True).finish()
    assert result["state"] == "submission_unknown"


async def test_two_controls_stay_not_unique_and_no_control_before_a_commit_is_not_a_thank_you(monkeypatch):
    two = await engine_for(monkeypatch, NoControlForm(controls=2), armed=True).finish()
    assert two == {"state": "needs_review", "reason": "Final submit control was not unique"}
    none = await engine_for(monkeypatch, NoControlForm(controls=0, receipt={"confirmation": "x"}), armed=False).finish()
    assert none["state"] == "needs_review" and "No final submit control" in none["reason"]
