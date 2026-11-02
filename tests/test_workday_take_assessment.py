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
    ASSESSMENT_SETTLE_SECONDS = 0

    def __init__(self):
        self.workday_page = Page(url=WORKDAY, closed=False)
        form = SimpleNamespace(page=self.workday_page, scan=self.scan, settle=self.settle, click=self.form_click,
                               return_to_opener=self.return_to_opener)
        self.e = SimpleNamespace(quality_gate=None, form=form, result=None)
        self.attempts, self.committed, self.events = Counter(), False, []
        self.clicked, self.form_clicks, self.filled, self.returned, self.launched = [], [], 0, 0, []
        self.controls, self.text, self.closes_on = [], STEP_TEXT, None

    def emit(self, kind, message, data=None):
        self.events.append(kind)

    async def scan(self):
        return {"url": self.page.url, "controls": self.controls, "fields": [], "errors": [], "automation": [],
                "headings": [], "text": self.text, "dialogs": []}

    async def settle(self, timeout=0):
        return None

    async def form_click(self, control_id, auth_control=False, step_control=False, launch=False):
        if launch:
            self.launched.append(control_id)
        else:
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
    # A launcher by its automation id, so the run returns here when a survey tab closes itself.
    assert adapter.launched == ["take"] and adapter.assessment_from == "acme.wd1.myworkdayjobs.com"


async def test_an_assessment_that_is_not_the_tax_credit_questionnaire_is_left_alone():
    adapter = Scripted()
    adapter.controls, adapter.text = [take()], "Take Assessment Complete the coding skills assessment"
    import jobpilot.adapters.base as base

    original, base.Adapter.on_form = base.Adapter.on_form, lambda self, obs: _none()
    try:
        await adapter.on_form(await adapter.scan())
    finally:
        base.Adapter.on_form = original
    assert adapter.launched == []


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
    assert adapter.launched == ["take"] and calls == ["save and continue"]


async def test_a_questionnaire_that_never_returns_stops_at_its_page_limit():
    adapter = await opened()
    adapter.open_questionnaire([{"id": "next", "label": "Next", "automation_id": ""}])
    for _ in range(40):
        assert await adapter.on_form(await adapter.scan()) is None
    result = await adapter.on_form(await adapter.scan())
    assert result["state"] == "waiting_browser" and "did not return" in result["reason"]


async def test_exits_are_never_taken_for_the_way_forward():
    adapter = await opened()
    adapter.open_questionnaire([{"id": "later", "label": "Finish later", "automation_id": ""},
                                {"id": "back", "label": "Back to application", "automation_id": ""},
                                {"id": "next", "label": "Next", "automation_id": ""}])
    await adapter.on_form(await adapter.scan())
    assert adapter.form_clicks == [("next", True)]


async def test_another_workday_host_is_the_application_not_the_questionnaire():
    adapter = await opened()
    adapter.e.form.page = Page(url="https://acme.wd1.myworkdaysite.com/en-US/recruiting/acme/careers/job/R-1/apply",
                               closed=False)
    assert not adapter.in_assessment() and adapter.handoff(adapter.page.url) is None


async def test_a_terminal_questionnaire_outcome_ends_the_guided_flow():
    adapter = await opened()
    adapter.open_questionnaire([{"id": "next", "label": "Next", "automation_id": ""}])
    adapter.attempts["assessment_pages"] = 40
    await adapter.intermediate_submit()
    assert adapter.e.result["state"] == "waiting_browser"


async def test_the_navigator_never_hands_the_questionnaire_to_another_ats():
    adapter = await opened()
    adapter.open_questionnaire([])
    # A questionnaire served from a registered ATS's domain (ADP has no adapter, so iCIMS stands in).
    adapter.e.form.page = Page(url="https://taxcredit.icims.com/survey", closed=False)
    engine = SimpleNamespace(armed=False, navigator_handed_off=False, page=adapter.page, fallback_mail_adapter=adapter,
                             job={"ats": "workday"})
    assert await BrowserEngine.navigator_handoff(engine) is None and not engine.navigator_handed_off


async def test_an_answer_that_posts_back_to_the_next_question_continues_the_questionnaire():
    """MAXIMUS's survey (Allison Transmission, 2026-10-25) loads its next question when a radio is clicked and never
    shows Next; the run gave up as "No way forward" after the first answer."""
    adapter = await opened()
    adapter.open_questionnaire([])
    questions = iter(["Are you a resident of the State of California?", "Have you received government assistance?"])
    current = {"label": next(questions)}

    async def scan():
        return {"url": adapter.page.url, "controls": [], "errors": [], "automation": [], "headings": [],
                "text": "Work Opportunity Tax Credit", "dialogs": [],
                "fields": [{"label": current["label"], "type": "radio", "options": ["Yes", "No"]}]}

    async def answer():
        adapter.filled += 1
        current["label"] = next(questions, current["label"])  # The click posts the page back to the next question.
        return {"ok": True}

    adapter.e.form.scan, adapter.fill_page = scan, answer
    assert await adapter.on_assessment() is None  # Moved on by itself.
    # The last question no longer changes and offers no way forward: hand the page to the guided flow.
    assert (await adapter.on_assessment())["fallback"] is True


async def test_a_page_whose_pop_up_opens_after_it_renders_is_scanned_again_before_giving_up():
    """ADP's "Quick tip before you begin ... Okay" (Montenson, 2026-10-28) opened after the statements page rendered;
    the scan 1.9 s after Next saw neither its 12 questions nor Next, and the run fell back to the navigator."""
    adapter = await opened()
    adapter.open_questionnaire([])
    scans = iter([[], [], [{"label": "Did you receive SNAP in the past 6 months?", "type": "radiogroup",
                            "options": ["Yes", "No"]}]])
    last = []

    async def scan():
        nonlocal last
        last = next(scans, last)
        return {"url": adapter.page.url, "controls": [], "errors": [], "automation": [], "headings": [],
                "text": "Work Opportunity Tax Credit Questionnaire", "dialogs": [], "fields": last}

    adapter.e.form.scan = scan
    assert await adapter.on_assessment() is None  # The settled rescan shows the questions: fill them next.


async def test_a_step_still_reported_incomplete_after_the_return_is_opened_once_more():
    """Montenson (Oracle → ADP, 2026-10-29): the questionnaire finished and returned, but the application still read the
    step as untaken; it is opened once more, never a third time."""
    adapter = await opened()
    adapter.e.form.page = adapter.workday_page
    obs = await adapter.scan()
    assert await adapter.launch_assessment(obs) is False  # The ordinary path opens it once per run.
    assert await adapter.launch_assessment(obs, again=True) is True
    assert await adapter.launch_assessment(obs, again=True) is False
    assert adapter.launched == ["take", "take"]


VOLUNTARY_TEXT = (
    "Take Assessment Fifth Third participates in the Work Opportunity Tax Credit program. This survey is voluntary and will "
    "not impact your employment. If you prefer not to complete, please click \"Take Assessment\" below, and within the link "
    "click \"Decline tax credit screening\"."
)


def decline_page(adapter):
    adapter.open_questionnaire([{"id": "next", "label": "Next", "automation_id": ""},
                                {"id": "decline", "label": "Decline tax credit screening.", "automation_id": ""}])
    adapter.text = "Employer Tax Credit Screening Full Name Street Address"


async def test_a_survey_the_employer_calls_voluntary_is_declined_with_its_own_control_and_confirmation():
    """Fifth Third (2026-11-02): the questionnaire was answered and e-signed, Workday showed Take Assessment again, and the run
    opened it four times. The employer's step says "If you prefer not to complete ... click 'Decline tax credit screening' ...
    this will allow you to navigate back to your application"; jobcredits.com confirms with "Opt Out" beside "Take Survey"."""
    adapter = Scripted()
    adapter.controls, adapter.text = [take()], VOLUNTARY_TEXT
    assert await adapter.on_form(await adapter.scan()) is None and adapter.assessment_voluntary
    decline_page(adapter)
    modal = [{"id": "confirm", "label": "Opt Out", "automation_id": ""}, {"id": "stay", "label": "Take Survey", "automation_id": ""}]
    original = adapter.form_click

    async def click(control_id, auth_control=False, step_control=False, launch=False):
        await original(control_id, auth_control, step_control, launch)
        if control_id == "decline":
            adapter.controls = adapter.controls + modal
        if control_id == "confirm":
            adapter.page.closed = True

    adapter.e.form.click = click
    assert await adapter.on_form(await adapter.scan()) is None
    assert [c for c, _ in adapter.form_clicks] == ["decline", "confirm"]
    assert adapter.filled == 0 and adapter.returned == 1  # nothing was answered or signed, and the run is back on Workday


async def test_a_required_survey_is_answered_not_declined_the_first_time():
    adapter = await opened()
    assert not getattr(adapter, "assessment_voluntary", False)
    decline_page(adapter)
    await adapter.on_form(await adapter.scan())
    assert adapter.filled == 1 and [c for c, _ in adapter.form_clicks] == ["next"]


async def test_a_finished_survey_the_application_still_demands_is_declined_on_the_second_opening():
    """Smithfield (Synergi): eight finished surveys and Workday still said "You must complete the assessment test"."""
    adapter = await opened()
    adapter.e.form.page = adapter.workday_page
    adapter.e.form.survey_finished = True
    demand = {**await adapter.scan(), "errors": ["Error - Page Error You must complete the assessment test to continue with your job application."]}
    assert await adapter.launch_assessment(demand, again=True) is True and adapter.assessment_unrecorded
    adapter.open_questionnaire([{"id": "next", "label": "Next", "automation_id": ""},
                                {"id": "out", "label": "Opt-Out", "automation_id": ""}])
    adapter.text = "Tax credit screening"
    adapter.closes_on = "out"
    await adapter.on_form(await adapter.scan())
    assert [c for c, _ in adapter.form_clicks] == ["out"] and adapter.filled == 0 and adapter.returned == 1


async def test_a_page_that_cannot_be_inspected_mid_redirect_is_read_again_not_given_up_on():
    adapter = await opened()
    adapter.open_questionnaire([])
    reads = iter([{"observation_failure": True, "fields": [], "controls": []},
                  {"observation_failure": True, "fields": [], "controls": []},
                  {"fields": [{"label": "Have you received SNAP?", "type": "radio", "options": ["Yes", "No"]}], "controls": []}])
    last = {}

    async def scan():
        nonlocal last
        last = next(reads, last)
        return {"url": adapter.page.url, "errors": [], "automation": [], "headings": [], "text": "Tax credit", "dialogs": [], **last}

    adapter.e.form.scan = scan
    seen = await adapter.assessment_scan()
    assert seen["fields"] and not seen.get("observation_failure")
