"""Workday's "Take Assessment" step: "Click here to complete the Work Opportunity Tax Credit Questionnaire".

Rentokil (2026-10-24): Save and Continue past it answered with a Workday server error on every attempt. Tyson Foods
(2026-10-24): the button opened TaxCreditCo's survey in a new tab; its last page says "Submit form", the guided flow
committed the application on it, the survey closed its own tab, and the unsubmitted application was recorded as an
unverifiable submission. The questionnaire is filled and finished with its own controls, then the run returns to
Workday; nothing on its host is the application's submission or confirmation."""

import json
from collections import Counter
from types import SimpleNamespace

from jobpilot.adapters.base import Step
from jobpilot.adapters.workday import Workday
from jobpilot.browser import BrowserEngine

WORKDAY = "https://acme.wd1.myworkdayjobs.com/en-US/careers/job/Analyst_R-1/apply"
QUESTIONNAIRE = "https://wotc.adp.com/questionnaire/statements"  # an ADP host, like Rentokil's
STEP_TEXT = "Take Assessment Click here to complete the Work Opportunity Tax Credit Questionnaire"


class Page(SimpleNamespace):
    def is_closed(self):
        return self.closed


class Scripted(Workday):
    def __init__(self):
        self.workday_page = Page(url=WORKDAY, closed=False)
        form = SimpleNamespace(page=self.workday_page, scan=self.scan, settle=self.settle, click=self.form_click,
                               return_to_opener=self.return_to_opener)
        self.e = SimpleNamespace(quality_gate=None, form=form)
        self.attempts, self.committed, self.events = Counter(), False, []
        self.clicked, self.form_clicks, self.filled, self.returned = [], [], 0, 0
        self.controls, self.text, self.closes_on = [], STEP_TEXT, None

    def emit(self, kind, message, data=None):
        self.events.append(kind)

    async def scan(self):
        return {"url": self.page.url, "controls": self.controls, "fields": [], "errors": [], "automation": [],
                "headings": [], "text": self.text, "dialogs": []}

    async def settle(self, timeout=0):
        return None

    async def form_click(self, control_id, auth_control=False, step_control=False):
        self.form_clicks.append((control_id, auth_control))
        if control_id == self.closes_on:
            self.page.closed = True

    async def return_to_opener(self):
        self.returned += 1
        self.e.form.page = self.workday_page

    async def click(self, button, auth=False, step=True):
        self.clicked.append(button["label"])

    async def dismiss_overlays(self):
        return None

    async def fill_page(self):
        self.filled += 1
        return {"ok": True}

    def open_questionnaire(self, controls):
        self.e.form.page = Page(url=QUESTIONNAIRE, closed=False)
        self.controls, self.text = controls, "Thank you for completing the questionnaire"


def take():
    return {"id": "take", "label": "Take Assessment", "automation_id": "inlineAssessmentButton"}


async def opened():
    adapter = Scripted()
    adapter.controls = [take(), {"id": "next", "label": "Save and Continue", "automation_id": "pageFooterNextButton"}]
    assert await adapter.on_form(await adapter.scan()) is None
    return adapter


async def test_the_tax_credit_assessment_is_taken_before_save_and_continue():
    adapter = await opened()
    assert adapter.clicked == ["Take Assessment"] and adapter.assessment_from == "acme.wd1.myworkdayjobs.com"


async def test_an_assessment_that_is_not_the_tax_credit_questionnaire_is_left_alone():
    adapter = Scripted()
    adapter.controls, adapter.text = [take()], "Take Assessment Complete the coding skills assessment"
    import jobpilot.adapters.base as base

    original, base.Adapter.on_form = base.Adapter.on_form, lambda self, obs: _none()
    try:
        await adapter.on_form(await adapter.scan())
    finally:
        base.Adapter.on_form = original
    assert adapter.clicked == []


async def _none():
    return None


async def test_questionnaire_pages_are_finished_by_their_own_submit_never_committed():
    adapter = await opened()
    adapter.open_questionnaire([{"id": "prev", "label": "Previous", "automation_id": ""},
                                {"id": "submit", "label": "Submit form", "automation_id": ""}])
    obs = await adapter.scan()
    # Its Submit and thank-you text are the questionnaire's: an ordinary form step, never a review or confirmation,
    # and its adp.com host is not a handoff to the ADP application adapter.
    assert adapter.classify_page(obs) == Step.FORM and adapter.handoff(QUESTIONNAIRE) is None
    assert await adapter.on_form(obs) is None
    assert adapter.filled == 1 and adapter.form_clicks == [("submit", True)] and not adapter.committed


async def test_a_survey_tab_that_closes_itself_returns_to_workday():
    adapter = await opened()
    adapter.open_questionnaire([{"id": "submit", "label": "Submit form", "automation_id": ""}])
    adapter.closes_on = "submit"
    assert await adapter.on_form(await adapter.scan()) is None
    assert adapter.returned == 1 and adapter.page.url == WORKDAY and not adapter.in_assessment()


async def test_the_guided_flows_submit_finishes_the_questionnaire_instead_of_the_application():
    adapter = await opened()
    assert await adapter.intermediate_submit() is None  # on Workday itself the application's submit is untouched
    adapter.open_questionnaire([{"id": "submit", "label": "Submit form", "automation_id": ""}])
    outcome = json.loads(await adapter.intermediate_submit())
    assert outcome["intermediate_step"] and outcome["executed"] and adapter.form_clicks == [("submit", True)]


async def test_the_application_commit_refuses_while_the_questionnaire_is_open():
    adapter = await opened()
    adapter.open_questionnaire([])
    engine = SimpleNamespace(armed=False, form=SimpleNamespace(referral_route=lambda: False),
                             fallback_mail_adapter=adapter)
    result = await BrowserEngine._finish(engine)
    assert result["state"] == "waiting_browser" and "questionnaire" in result["reason"]


async def test_back_on_workday_the_assessment_is_not_reopened():
    adapter = await opened()
    adapter.e.form.page = adapter.workday_page
    calls = []
    import jobpilot.adapters.base as base

    async def advance(self, obs):
        calls.append("save and continue")

    original, base.Adapter.on_form = base.Adapter.on_form, advance
    try:
        await adapter.on_form(await adapter.scan())
    finally:
        base.Adapter.on_form = original
    assert adapter.clicked == ["Take Assessment"] and calls == ["save and continue"]


async def test_a_questionnaire_that_never_returns_stops_after_twelve_pages():
    adapter = await opened()
    adapter.open_questionnaire([{"id": "next", "label": "Next", "automation_id": ""}])
    for _ in range(12):
        assert await adapter.on_form(await adapter.scan()) is None
    result = await adapter.on_form(await adapter.scan())
    assert result["state"] == "waiting_browser" and "did not return" in result["reason"]
