"""The same failing field write is not repeated on an unchanged page.

Production 2026-10-26..28: Avature's "Country" was written about 32 times per run, iCIMS "Phone Country Code" 25, Workday
"Field of Study" 8-10 and iCIMS "Desired Location" took 19 model decisions, each failure costing up to 12 s; 178 minutes
in three days were third-or-later tries of one field in one run. JazzHR read the same clean 441-field page back 48 times.
"""

import pytest

from jobpilot.answers import Resolver
from jobpilot.forms import FormSession
from jobpilot.schemas import Profile

PAGE = '<label for=p>Phone</label><input id=p required type=tel><label for=z>Notes</label><input id=z>'


class Refused(ValueError):
    """What a picker raises when its offered choices do not hold the answer."""

    def __init__(self, offered=None):
        super().__init__("The option is not offered")
        self.offered = offered


async def session(page, tmp_path, service, html=PAGE, phone="2405550100"):
    resume = tmp_path / "r.pdf"
    resume.write_bytes(b"%PDF")
    await page.set_content(html)
    events = []
    form = FormSession(
        page,
        Resolver(Profile(phone=phone), service.db, service.config),
        resume,
        lambda kind, message, data=None: events.append((kind, message, data or {})),
    )
    return form, events


def failing(form, raised):
    """Replace the field write with one that always raises; returns the list of attempted values."""
    writes = []

    async def fill(field, value):
        writes.append(value)
        raise raised() if callable(raised) else raised

    form.fill = fill
    return writes


def held(events):
    return [e for e in events if e[0] == "field_retry_skipped"]


async def test_a_write_that_failed_twice_the_same_way_is_not_made_a_third_time(page, tmp_path, service):
    form, events = await session(page, tmp_path, service)
    writes = failing(form, Refused(["Canada", "India"]))
    await form.fill_current()
    await form.fill_current()
    assert len(writes) == 2
    report = await form.fill_current()
    await form.fill_current()
    assert len(writes) == 2, "a third identical write was attempted"
    # The field and the reason are a concrete blocker, not a silent skip.
    blocker = next(p for p in report["pending"] if p["question"] == "Phone")
    assert blocker["blocker_type"] == "control_failure" and blocker["retryable"]
    assert "failed 2 times on an unchanged page" in blocker["reason"] and "not tried again" in blocker["reason"]
    assert [e[2]["failures"] for e in held(events)] == [2], "announced once, with its count"
    assert sum(1 for e in events if e[0] == "field_error") == 2


async def test_the_second_failure_already_says_it_will_not_be_retried(page, tmp_path, service):
    form, _ = await session(page, tmp_path, service)
    failing(form, Refused())
    first = await form.fill_current()
    second = await form.fill_current()
    assert "will not be tried again" not in next(p for p in first["pending"] if p["question"] == "Phone")["reason"]
    assert "will not be tried again" in next(p for p in second["pending"] if p["question"] == "Phone")["reason"]


async def test_a_held_back_write_does_not_ask_the_model(page, tmp_path, service, monkeypatch):
    form, _ = await session(page, tmp_path, service)
    failing(form, Refused())
    await form.fill_current()
    await form.fill_current()

    async def no_model(fields, **kwargs):
        raise AssertionError("the answer model was asked about a held-back field")

    monkeypatch.setattr(form.resolver, "resolve", no_model)
    await form.fill_current()
    assert form.stuck_fields() == ["Phone"]


async def test_a_changed_page_allows_the_write_again(page, tmp_path, service):
    form, _ = await session(page, tmp_path, service)
    writes = failing(form, Refused())
    await form.fill_current()
    await form.fill_current()
    await form.fill_current()
    assert len(writes) == 2 and form.stuck_fields() == ["Phone"]
    # A neighbouring answer changed: the earlier failure says nothing about this page.
    await page.fill("#z", "something new")
    await form.fill_current()
    assert len(writes) == 3 and form.stuck_fields() == []
    # A new field appeared.
    await form.fill_current()
    assert len(writes) == 4
    await form.fill_current()
    assert len(writes) == 4 and form.stuck_fields() == ["Phone"]
    await page.evaluate("document.body.insertAdjacentHTML('beforeend', '<label for=q>Extra</label><input id=q>')")
    await form.fill_current()
    assert len(writes) == 5


async def test_new_offered_choices_make_the_next_failure_a_new_one(page, tmp_path, service):
    form, _ = await session(page, tmp_path, service)
    offered = iter([["Canada"], ["Canada", "India"], ["Canada", "India"], ["Canada", "India"]])
    writes = failing(form, lambda: Refused(next(offered)))
    for _ in range(4):
        await form.fill_current()
    # 1 (Canada) | 2 (more options: counts as a first failure again) | 3 (same options: held back) | 4 skipped
    assert len(writes) == 3
    assert form.stuck_fields() == ["Phone"]


async def test_a_different_answer_is_a_different_write(page, tmp_path, service):
    form, _ = await session(page, tmp_path, service)
    writes = failing(form, Refused())
    await form.fill_current()
    await form.fill_current()
    await form.fill_current()
    assert len(writes) == 2
    form.resolver.profile = Profile(phone="2405550199")
    await form.fill_current()
    assert len(writes) == 3 and writes[2] != writes[1]


async def test_a_different_failure_is_not_counted_with_an_earlier_one(page, tmp_path, service):
    form, _ = await session(page, tmp_path, service)
    errors = iter([Refused(), TimeoutError(), Refused(), TimeoutError()])
    writes = failing(form, lambda: next(errors))
    for _ in range(4):
        await form.fill_current()
    assert len(writes) == 4 and form.stuck_fields() == []


async def test_a_write_that_succeeds_clears_the_failure(page, tmp_path, service):
    form, _ = await session(page, tmp_path, service)
    real = form.fill
    failing(form, Refused())
    await form.fill_current()
    assert form.field_failures
    form.fill = real
    await form.fill_current()
    assert not form.field_failures
    assert await page.locator("#p").input_value() != ""


class Engine:
    """Just enough of BrowserEngine for run_desktop_navigator: every method the loop calls does nothing."""

    armed = False
    result = None
    auto_submitted = False
    submit_rerouted = False
    reroute_submit_hint = "route"
    fallback_mail_adapter = None

    def __init__(self, form, service, page):
        self.form, self.service, self.page = form, service, page
        self.db, self.config = service.db, service.config
        self.run_id = "run-a"
        self.run_record = {"packet": {}, "mode": "dry_run"}
        self.job = {"id": "j1", "company": "ExampleSoft", "title": "Analyst", "ats": "custom", "url": page.url}
        self.events = []

    def emit(self, kind, message="", data=None):
        self.events.append((kind, message, data or {}))

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)

        async def nothing(*args, **kwargs):
            return None

        return nothing

    def current_repair_instruction(self):
        return ""

    async def checkpoint_draft(self):
        return None

    async def trace(self, *args, **kwargs):
        return None

    async def recover_empty_observation(self, obs):
        return obs

    async def await_successfactors_handoff(self, obs):
        return obs

    async def break_stall(self, decision, target):
        return ""

    def successfactors_application_step(self, obs):
        return False

    def recovery_exhausted_reason(self, *args, **kwargs):
        return "exhausted"


@pytest.mark.parametrize("held_back,decisions", [(True, 3), (False, 5)])
async def test_the_navigator_stops_choosing_a_fill_that_cannot_write(
    page, tmp_path, service, monkeypatch, held_back, decisions
):
    from jobpilot import models
    from jobpilot.browser import BrowserEngine, NavigationDecision

    form, _ = await session(page, tmp_path, service)
    await form.scan()
    phone = next(f for f in form.fields.values() if f["label"] == "Phone")
    if held_back:
        for _ in range(2):
            form.record_write_failure(phone, "x", Refused())
        assert form.stuck_fields() == ["Phone"]
    fills = []

    async def fill_current(**kwargs):
        fills.append(1)
        return {"ok": False, "pending": [], "problems": ["Required field incomplete: Phone"]}

    async def recover_controls(report):
        return report

    form.fill_current, form.recover_controls = fill_current, recover_controls
    asked = []

    async def structured(*args, **kwargs):
        asked.append(1)
        return NavigationDecision(action="fill")

    monkeypatch.setattr(models, "structured", structured)
    monkeypatch.setattr(models, "effective_thinking", lambda *a: "disabled")
    engine = Engine(form, service, page)
    await BrowserEngine.run_desktop_navigator(engine)
    assert len(asked) == decisions, (len(asked), [e[0] for e in engine.events])
    assert len(fills) == (1 if held_back else 2)
    assert "navigation_stalled" in [e[0] for e in engine.events]
