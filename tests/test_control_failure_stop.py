"""Humana's Degree (Workday, 2026-10-30): inspect_options was refused as covered, click as "'Degree' is a dropdown field,
not a button; use fill", and fill as a repeated strategy. The navigator then cycled the three for seven more decisions:
the repeat refusal counts one action on one control and every other action reset it. A control whose last three results
failed ends the recovery; and the click refusal no longer sends the model back to a fill that already failed."""

import json
from types import SimpleNamespace

import pytest
from playwright.async_api import Error as PlaywrightError

from jobpilot.browser_contracts import ControlFailures, control_failed, control_key
from jobpilot.forms import FormSession

DEGREE = {"id": "f0-x-jp1", "label": "Degree", "type": "dropdown", "group": "Education 1", "section": "", "value": ""}


def test_a_result_that_did_not_drive_the_control_is_a_failure():
    refused = json.dumps({"executed": False, "failure_category": "covered"})
    assert control_failed("inspect_options", "c1", refused)
    assert control_failed("click", "c1", refused)
    # An inspection that read no option, and a fill that left this control pending.
    assert control_failed("inspect_options", "c1", json.dumps({"options": [], "option_count": 0}))
    pending = json.dumps({"ok": False, "pending": [{"field_id": "c1"}, {"field_id": "c2"}]})
    assert control_failed("fill", "c1", pending) and not control_failed("fill", "c3", pending)
    # Progress, or an outcome that is not a result, is not a failure.
    assert not control_failed("inspect_options", "c1", json.dumps({"options": [{"text": "Bachelor's Degree"}]}))
    assert not control_failed("click", "c1", json.dumps({"executed": True, "verified": False}))
    assert not control_failed("click", "c1", "") and not control_failed("click", "c1", "plain text")


def test_three_failures_in_a_row_on_one_control_stop_it_whatever_the_actions():
    failures = ControlFailures()
    other = {**DEGREE, "id": "f0-x-jp2", "label": "Field of Study"}
    assert not failures.record(control_key(DEGREE), True)
    assert not failures.record(control_key(other), True), "another control has its own count"
    assert not failures.record(control_key(DEGREE), True)
    assert failures.record(control_key(DEGREE), True)
    # A success ends the streak; a changed value is a new situation.
    again = ControlFailures()
    again.record(control_key(DEGREE), True)
    again.record(control_key(DEGREE), True)
    assert not again.record(control_key(DEGREE), False)
    assert not again.record(control_key(DEGREE), True)
    changed = ControlFailures()
    changed.record(control_key(DEGREE), True)
    changed.record(control_key(DEGREE), True)
    assert not changed.record(control_key({**DEGREE, "value": "Bachelor's Degree"}), True)


class Refused(ValueError):
    offered = None


def form_with(field):
    form = FormSession.__new__(FormSession)
    form.fields, form.field_failures, form.last_observation = {field["id"]: field}, {}, {}
    return form


def test_the_click_refusal_names_the_next_action_once_fill_has_failed():
    form = form_with(DEGREE)
    # Nothing failed yet: fill is still the right advice.
    assert "use fill" in form.not_a_button(DEGREE)
    for _ in range(2):
        form.record_write_failure(DEGREE, "BE", PlaywrightError("<div> intercepts pointer events"))
    reason = form.not_a_button(DEGREE)
    assert "use fill" not in reason and "will not be repeated" in reason
    assert "covered" in reason and "inspect_options" in reason and len(reason) < 240


def test_the_stop_names_the_control_and_is_a_control_failure():
    events = []
    engine = SimpleNamespace(emit=lambda *a: events.append(a), form=SimpleNamespace(last_problems=[
        "Required field incomplete: Degree"]))
    from jobpilot.browser import BrowserEngine

    result = BrowserEngine.stuck_control_result(engine, DEGREE, ControlFailures())
    assert result["state"] == "waiting_browser" and result["blocker_type"] == "control_failure"
    assert "'Degree' failed 3 results in a row" in result["reason"] and "Degree" in result["reason"]
    assert events[0][0] == "navigation_stalled"


async def test_the_navigator_stops_after_three_failed_results_on_one_control(page, tmp_path, service, monkeypatch):
    from itertools import cycle

    from jobpilot import models
    from jobpilot.browser import BrowserEngine, NavigationDecision
    from test_repeated_field_write import Engine, session

    form, _ = await session(page, tmp_path, service)
    await form.scan()
    phone = next(f for f in form.fields.values() if f["label"] == "Phone")

    async def fill_current(**kwargs):
        return {"ok": False, "pending": [{"field_id": phone["id"]}], "problems": ["Required field incomplete: Phone"]}

    async def recover_controls(report):
        return report

    async def covered(field, query=""):
        raise PlaywrightError("<div> intercepts pointer events")

    form.fill_current, form.recover_controls, form.inspect_control_options = fill_current, recover_controls, covered
    actions, asked = cycle(["inspect_options", "click", "fill"]), []

    async def structured(*args, **kwargs):
        asked.append(1)
        return NavigationDecision(action=next(actions), control_id=phone["id"])

    async def before_click(control_id):
        return None

    monkeypatch.setattr(models, "structured", structured)
    monkeypatch.setattr(models, "effective_thinking", lambda *a: "disabled")
    engine = Engine(form, service, page)
    engine.email_verification = SimpleNamespace(before_click=before_click)
    engine.answer_verdict = lambda *args: None
    engine.stuck_control_result = lambda target, failures: BrowserEngine.stuck_control_result(engine, target, failures)
    await BrowserEngine.run_desktop_navigator(engine)
    # Three decisions, three different actions, one control: the third failure ends it (it used to run on).
    assert len(asked) == 3, (len(asked), [e[0] for e in engine.events])
    assert engine.result["blocker_type"] == "control_failure" and "'Phone' failed 3 results" in engine.result["reason"]
    assert [e[0] for e in engine.events].count("navigation_stalled") == 1
