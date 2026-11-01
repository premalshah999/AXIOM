"""A page that already reads back clean is advanced with its own Next control, not filled again.

Paylocity (2026-10-26): 298 fields verified, resume attached, nothing pending, and the model chose the same page-wide fill
five more times beside a visible "Next Step" until the run stalled; the same shape ended ADP "Continue", Oracle "Next" and
Rippling runs (104 of 328 stalled guided runs, 10-25..31). The scenarios drive the real navigator loop on a scripted form.
"""

from types import SimpleNamespace

from jobpilot import continuous_agent, models
from jobpilot import browser as browser_module
from jobpilot.browser import BrowserEngine, NavigationDecision
from test_navigator_submit_reroute import Engine, Form


class CleanForm(Form):
    def __init__(self, labels=("Next Step",), clean=True):
        super().__init__()
        self.buttons = {f"b{i}": {"id": f"b{i}", "label": label, "type": "button"} for i, label in enumerate(labels)}
        self.clean, self.fills, self.clicked = clean, 0, []

    async def fill_current(self, **kwargs):
        self.fills += 1
        if self.clean:
            return {"ok": True, "pending": [], "problems": [], "verified_fields": 9}
        return {"ok": False, "pending": [], "problems": ["Required field incomplete: Phone"]}

    async def verify(self):
        return {"ok": False}

    async def recover_controls(self, report):
        return report

    async def click(self, control_id, **kwargs):
        self.clicked.append(control_id)
        self.last_action = SimpleNamespace(model_dump=lambda: {"executed": True})
        self.clicks += 1


def drive(monkeypatch, engine):
    async def structured(*args, **kwargs):
        return NavigationDecision(action="fill")

    monkeypatch.setattr(models, "structured", structured)
    monkeypatch.setattr(models, "effective_thinking", lambda *a: "disabled")
    monkeypatch.setattr(continuous_agent, "navigation_context", lambda form, obs: {})
    monkeypatch.setattr(browser_module, "navigation_supported_answers", lambda form, obs: {})

    import asyncio

    async def one_page_scan():
        return {**await original(), "progress_fingerprint": "same"}

    original = engine.form.scan
    engine.form.scan = one_page_scan
    asyncio.run(BrowserEngine.run_desktop_navigator(engine))


def make(service, form):
    engine = Engine(form, service, {"state": "confirmed", "reason": "done"})
    engine.checkpoint_draft = lambda: _none()
    engine.advance_control = lambda: BrowserEngine.advance_control(engine)
    return engine


async def _none():
    return None


def test_a_clean_page_the_model_keeps_filling_is_advanced_with_its_next_control(service, monkeypatch):
    form = CleanForm()
    engine = make(service, form)
    drive(monkeypatch, engine)
    assert form.clicked and form.clicked[0] == "b0", [e[0] for e in engine.events]
    assert "advance_clean_page" in [e[0] for e in engine.events]


def test_it_is_advanced_once_per_page_then_the_repeat_limits_apply(service, monkeypatch):
    form = CleanForm()
    engine = make(service, form)
    drive(monkeypatch, engine)
    assert form.clicked == ["b0"]
    assert "navigation_stalled" in [e[0] for e in engine.events]


def test_a_page_with_unfinished_fields_is_not_advanced(service, monkeypatch):
    form = CleanForm(clean=False)
    engine = make(service, form)
    drive(monkeypatch, engine)
    assert form.clicked == []


def test_two_different_advance_controls_are_not_guessed_between(service, monkeypatch):
    form = CleanForm(labels=("Next Step", "Continue"))
    engine = make(service, form)
    drive(monkeypatch, engine)
    assert form.clicked == []
