"""Workday's "Take Assessment" step (Rentokil/Terminix, 2026-10-24): "Click here to complete the Work Opportunity Tax
Credit Questionnaire". Save and Continue past it answered with a Workday server error on every attempt. The button
opens ADP's questionnaire in the same tab; its Next/Submit finish only the questionnaire, which returns to Workday."""

from collections import Counter
from types import SimpleNamespace

from jobpilot.adapters.base import Step
from jobpilot.adapters.workday import Workday

WORKDAY = "https://acme.wd1.myworkdayjobs.com/en-US/careers/job/Analyst_R-1/apply"
QUESTIONNAIRE = "https://wotc.adp.com/questionnaire/statements"  # an ADP host, like the real one


class Scripted(Workday):
    def __init__(self):
        form = SimpleNamespace(page=SimpleNamespace(url=WORKDAY), scan=self.scan, settle=self.settle,
                               click=self.form_click)
        self.e = SimpleNamespace(quality_gate=None, form=form)
        self.attempts, self.committed, self.events = Counter(), False, []
        self.clicked, self.form_clicks, self.filled = [], [], 0
        self.controls = []

    def emit(self, kind, message, data=None):
        self.events.append(kind)

    async def scan(self):
        return {"url": self.page.url, "controls": self.controls, "fields": [], "errors": [], "automation": [],
                "headings": [], "text": "Thank you for completing the questionnaire", "dialogs": []}

    async def settle(self, timeout=0):
        return None

    async def form_click(self, control_id, step_control=False):
        self.form_clicks.append(control_id)

    async def click(self, button, auth=False, step=True):
        self.clicked.append(button["label"])

    async def dismiss_overlays(self):
        return None

    async def fill_page(self):
        self.filled += 1
        return {"ok": True}


def take():
    return {"id": "take", "label": "Take Assessment", "automation_id": "inlineAssessmentButton"}


async def test_the_assessment_is_taken_before_save_and_continue():
    adapter = Scripted()
    adapter.controls = [take(), {"id": "next", "label": "Save and Continue", "automation_id": "pageFooterNextButton"}]
    assert await adapter.on_form(await adapter.scan()) is None
    assert adapter.clicked == ["Take Assessment"] and adapter.assessment_from == "acme.wd1.myworkdayjobs.com"


async def test_questionnaire_pages_are_filled_and_finished_by_their_own_controls_never_committed():
    adapter = Scripted()
    adapter.controls = [take()]
    await adapter.on_form(await adapter.scan())
    adapter.page.url = QUESTIONNAIRE
    adapter.controls = [{"id": "prev", "label": "Previous", "automation_id": ""},
                        {"id": "submit", "label": "Submit", "automation_id": ""}]
    obs = await adapter.scan()
    # Its Submit and thank-you text are the questionnaire's: an ordinary form step, never a review or confirmation.
    assert adapter.classify_page(obs) == Step.FORM
    assert adapter.handoff(QUESTIONNAIRE) is None  # never handed to the ADP application adapter
    assert await adapter.on_form(obs) is None
    assert adapter.filled == 1 and adapter.form_clicks == ["submit"] and not adapter.committed


async def test_back_on_workday_the_assessment_is_not_reopened():
    adapter = Scripted()
    adapter.controls = [take()]
    await adapter.on_form(await adapter.scan())
    adapter.page.url = WORKDAY
    assert not adapter.in_assessment()
    calls = []

    async def advance(obs):
        calls.append("save and continue")

    import jobpilot.adapters.base as base

    original = base.Adapter.on_form
    base.Adapter.on_form = lambda self, obs: advance(obs)
    try:
        await adapter.on_form(await adapter.scan())
    finally:
        base.Adapter.on_form = original
    assert adapter.clicked == ["Take Assessment"] and calls == ["save and continue"]


async def test_a_questionnaire_that_never_returns_stops_after_twelve_pages():
    adapter = Scripted()
    adapter.controls = [take()]
    await adapter.on_form(await adapter.scan())
    adapter.page.url = QUESTIONNAIRE
    adapter.controls = [{"id": "next", "label": "Next", "automation_id": ""}]
    for _ in range(12):
        assert await adapter.on_form(await adapter.scan()) is None
    result = await adapter.on_form(await adapter.scan())
    assert result["state"] == "waiting_browser" and "did not return" in result["reason"]
