"""AT&T (Workday, 2026-10-27): its "Take Assessment" step says only "This information is for tax purposes and will not
be shared with AT&T." The questionnaire was never opened; Save and Continue answered "Error - Page Error: You must
complete the assessment test to continue with your job application", which read as a Workday server error, and two
draft restarts returned to the same untaken assessment."""

from types import SimpleNamespace

from jobpilot.browser import BrowserEngine

from test_workday_take_assessment import Scripted, take

ATT = ("Take Assessment * Indicates a required field Please fill out the following information. This information is "
       "for tax purposes and will not be shared with AT&T. Take Assessment")


async def test_a_take_assessment_step_for_tax_purposes_opens_the_questionnaire():
    adapter = Scripted()
    adapter.controls = [take(), {"id": "next", "label": "Save and Continue", "automation_id": "pageFooterNextButton"}]
    adapter.text = ATT
    assert await adapter.on_form(await adapter.scan()) is None
    assert adapter.launched == ["take"]


class Body:
    def __init__(self, text):
        self.text = text

    def locator(self, selector):
        return self

    async def inner_text(self, timeout=0):
        return self.text


def engine(text):
    eng = BrowserEngine.__new__(BrowserEngine)
    eng.job, eng.armed, eng.page, eng.result = {"ats": "workday", "url": "https://acme.test/job"}, False, Body(text), None
    eng.emit = lambda *a, **k: (_ for _ in ()).throw(AssertionError("restarted the draft"))
    return eng


async def test_a_page_error_that_states_the_steps_requirement_is_not_a_server_error():
    text = ("Errors Found\n1.\nError - Page Error\nYou must complete the assessment test to continue with your job "
            "application.\n" + ATT)
    assert await BrowserEngine.recover_workday_error(engine(text)) is False


async def test_a_server_error_still_restarts_the_draft():
    eng = engine("Something went wrong. Please refresh the page and try again.")
    events = []
    eng.emit = lambda kind, *a, **k: events.append(kind)
    eng.form = SimpleNamespace(reset_document_verification=lambda: None)

    async def goto(*a, **k):
        raise RuntimeError("navigated")

    eng.page.goto = goto
    try:
        await BrowserEngine.recover_workday_error(eng)
    except RuntimeError:
        pass
    assert events == ["browser_recovery"] and eng.workday_error_restarts == 1
