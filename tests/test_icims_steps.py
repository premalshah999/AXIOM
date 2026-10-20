"""iCIMS's progress bar, written for screen readers (ICE 2026-10-19): "Step 2 of 4. Voluntary Self-Identification
(Current Step)". Only the last step submits the application. ICE's run committed on EEO's Submit, waited for a receipt
and stopped at "Job Specific Questions" as submission_unknown. In Portal Specific Forms each form but the last names
its successor in a hidden "next" (REI Systems, network-captured)."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

from jobpilot.adapters.base import Adapter, Step
from support_icims import adapter

EEO = "https://careers-acme.icims.com/jobs/42/java-developer/eeo?in_iframe=1"
NBSP = " "


def step_page(current, total, name, url=EEO, label="Submit"):
    bar = "\n".join(
        f"Step {NBSP} {n} {NBSP} of {NBSP} {total}. {NBSP} Step {n}"
        + ("(Current Step)" if n == current else "(Completed Step)" if n < current else "(Incomplete Step)")
        for n in range(1, total + 1)
    ).replace(f"Step {current}(Current", f"{name}(Current")
    return {
        "url": url,
        "fields": [{"id": "gender", "type": "select", "label": "Gender", "required": False, "value": "", "error": ""}],
        "controls": [
            {"id": "submit", "label": label, "href": "", "automation_id": "", "aria_label": ""},
            {"id": "poster", "label": "EEO Policy Statement", "href": "/eeo.pdf", "automation_id": "", "aria_label": ""},
        ],
        "errors": [],
        "headings": [name],
        "text": "Java Developer\n" + bar + "\n" + name,
    }


def icims(service, obs, continues=False):
    value = adapter(service, obs)
    del value.click  # the real ICIMS.click
    value.form.scan = AsyncMock(return_value=obs)
    value.form.settle = AsyncMock()
    value.e.form.page.frames = [SimpleNamespace(evaluate=AsyncMock(return_value=continues))]
    value.committed = False
    return value


def test_the_progress_bar_is_read(service):
    obs = step_page(2, 4, "Voluntary Self-Identification")
    value = icims(service, obs)
    assert value.application_step(obs) == (2, 4, "Voluntary Self-Identification")
    assert value.classify(obs) == Step.FORM
    assert value.find_next(obs)["label"] == "Submit" and value.find_final(obs) is None


def test_the_last_step_keeps_its_submit_final(service):
    obs = step_page(4, 4, "Portal Specific Forms")
    value = icims(service, obs)
    assert value.find_final(obs)["label"] == "Submit"
    assert value.find_next(obs) is None or value.find_next(obs)["label"] != "Submit"


async def test_an_earlier_steps_submit_is_clicked_as_a_step(service, monkeypatch):
    parent = AsyncMock(return_value=None)
    monkeypatch.setattr(Adapter, "click", parent)
    obs = step_page(3, 4, "Job Specific Questions")
    await icims(service, obs).click(obs["controls"][0])
    assert parent.await_args.kwargs["auth"] is True


async def test_the_guided_submit_opens_the_next_step_without_a_commit(service, monkeypatch):
    parent = AsyncMock(return_value=None)
    monkeypatch.setattr(Adapter, "click", parent)
    obs = step_page(2, 4, "Voluntary Self-Identification")
    outcome = json.loads(await icims(service, obs).intermediate_submit())
    assert outcome["intermediate_step"] is True and parent.await_args.kwargs["auth"] is True
    # On the last step only a packet form that names its successor is a step.
    last = step_page(4, 4, "Portal Specific Forms")
    assert await icims(service, last, continues=False).intermediate_submit() is None
    assert json.loads(await icims(service, last, continues=True).intermediate_submit())["intermediate_step"] is True


async def test_review_opens_the_next_packet_form_instead_of_committing(service, monkeypatch):
    parent = AsyncMock(return_value=None)
    monkeypatch.setattr(Adapter, "click", parent)
    review = AsyncMock(return_value={"state": "confirmed"})
    monkeypatch.setattr(Adapter, "on_review", review)
    last = step_page(4, 4, "Portal Specific Forms")
    assert await icims(service, last, continues=True).on_review(last) is None
    review.assert_not_awaited()
    assert await icims(service, last, continues=False).on_review(last) == {"state": "confirmed"}


def test_the_last_steps_form_counter_marks_form_one_of_two_as_a_step(service):
    first = step_page(4, 4, "Portal Specific Forms (1/2)")
    value = icims(service, first)
    assert value.step_before_last(first) and value.find_final(first) is None
    assert value.find_next(first)["label"] == "Submit"
    last = step_page(4, 4, "Portal Specific Forms (2/2)")
    assert not icims(service, last).step_before_last(last)
    assert icims(service, last).find_final(last)["label"] == "Submit"


async def test_a_step_whose_fields_have_not_rendered_is_scanned_again(service, monkeypatch):
    fallback = AsyncMock(return_value={"fallback": True})
    monkeypatch.setattr(Adapter, "on_unknown", fallback)
    monkeypatch.setattr("asyncio.sleep", AsyncMock())
    empty = {**step_page(2, 4, "Voluntary Self-Identification"), "fields": []}
    value = icims(service, empty)
    for _ in range(3):
        assert await value.on_unknown(empty) is None
    assert await value.on_unknown(empty) == {"fallback": True}  # three waits per page, then the guided flow
    fallback.assert_awaited_once()


async def test_a_guided_click_on_a_step_submit_goes_through_the_adapter():
    from jobpilot.browser import BrowserEngine

    adapter_ = SimpleNamespace(intermediate_submit=AsyncMock(return_value='{"intermediate_step": true}'))
    engine = SimpleNamespace(
        armed=False,
        fallback_mail_adapter=adapter_,
        form=SimpleNamespace(buttons={"s": {"label": "Submit"}, "l": {"label": "Finish Later"}}),
    )
    assert await BrowserEngine.adapter_step_click(engine, "s") == '{"intermediate_step": true}'
    assert await BrowserEngine.adapter_step_click(engine, "l") is None  # never a save-for-later
    engine.armed = True
    assert await BrowserEngine.adapter_step_click(engine, "s") is None  # after the commit nothing is rerouted


async def test_a_job_page_whose_content_frame_has_not_loaded_is_scanned_again(service, monkeypatch):
    fallback = AsyncMock(return_value={"fallback": True})
    monkeypatch.setattr(Adapter, "on_unknown", fallback)
    monkeypatch.setattr("asyncio.sleep", AsyncMock())
    wrapper = {"url": "https://talent-acme.icims.com/jobs/1718/engineer/job", "fields": [], "controls": [], "errors": [],
               "headings": ["Digitally transform your business"], "text": "Solutions Resources About Us"}
    value = icims(service, wrapper)
    assert await value.on_unknown(wrapper) is None


def test_a_resumed_icims_run_restarts_from_the_posting():
    from jobpilot.browser import BrowserEngine

    def engine(url):
        return SimpleNamespace(job={"ats": "icims", "url": "https://eb-acme.icims.com/jobs/9917/ai-engineer/job"},
                               run_record={"packet": {}}, desktop_resume_state=lambda saved: {"url": url})

    stale = "https://eb-acme.icims.com/jobs/9917/ai-engineer/candidate?from=login&csrf=X&hashed=1&uploadResume=1"
    assert BrowserEngine.desktop_resume_route(engine(stale), {}) is None
    posting = "https://eb-acme.icims.com/jobs/9917/ai-engineer/job?in_iframe=1"
    assert BrowserEngine.desktop_resume_route(engine(posting), {}) == posting


def test_a_wrapped_email_step_is_still_the_email_step(service):
    obs = {"url": "https://careers-acme.icims.com/jobs/1730/engineer/login", "errors": [], "headings": ["Enter Your Information"],
           "text": "Enter Your Information", "controls": [{"id": "n", "label": "Next", "href": "", "automation_id": "", "aria_label": ""}],
           "fields": [
               {"id": "s1", "type": "text", "label": "Search", "required": False, "value": "", "automation_id": ""},
               {"id": "e", "type": "email", "label": "Email", "required": False, "value": "", "automation_id": ""},
               {"id": "c", "type": "checkbox", "label": "I have read the Privacy Notice", "required": True, "value": "",
                "automation_id": ""},
           ]}
    assert icims(service, obs).classify(obs) == Step.EMAIL_ENTRY
