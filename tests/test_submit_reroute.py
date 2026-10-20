"""A plain click on the real submit control is routed through the verified submit path once.

Toast (Greenhouse): the form was complete and verified, the model clicked "Apply now!" as an
ordinary click, the guard said "use submit_application", and the run looped on another control.
"""

from types import SimpleNamespace

from jobpilot.continuous_agent import ApplicationDecision

from test_continuous_stop import FakeEngine, FakeForm, run_drive

HINT = "This may submit the application; use submit_application"


class SubmitForm(FakeForm):
    def __init__(self, button_type="submit", hint=HINT):
        super().__init__(required_open=False)
        self.buttons = {"b1": {"label": "Apply now!", "type": button_type}}
        self.hint = hint
        self.clicks = 0
        self.last_action = SimpleNamespace(model_dump=lambda: {"executed": True})
        self.page = None

    def locator(self, control_id):
        async def evaluate(script):
            return False

        return SimpleNamespace(evaluate=evaluate)

    async def click(self, control_id, **kwargs):
        self.clicks += 1
        raise ValueError(self.hint)


def engine_hooks(monkeypatch, finish_result):
    calls = []

    async def finish(self, **kwargs):
        calls.append(1)
        return finish_result

    async def before_click(control_id):
        return None

    monkeypatch.setattr(FakeEngine, "finish", finish, raising=False)
    monkeypatch.setattr(FakeEngine, "reroute_submit_hint", "use submit_application", raising=False)
    monkeypatch.setattr(FakeEngine, "submit_rerouted", False, raising=False)
    monkeypatch.setattr(
        FakeEngine, "recoverable_result", lambda self, r: bool(r.get("recoverable")), raising=False
    )
    monkeypatch.setattr(
        FakeEngine, "email_verification", SimpleNamespace(before_click=before_click), raising=False
    )
    return calls


def clicker(n, form):
    return (
        ApplicationDecision(action="click", control_id="b1") if n == 1 else ApplicationDecision(action="wait")
    )


def test_refused_plain_click_on_submit_control_runs_the_verified_submit(monkeypatch):
    calls = engine_hooks(monkeypatch, {"state": "confirmed", "reason": "done"})
    engine, payloads = run_drive(monkeypatch, SubmitForm(), clicker)
    assert calls == [1]
    assert engine.result == {"state": "confirmed", "reason": "done"}
    assert [e for e in engine.events if e[0] == "submit_rerouted"]
    assert len(payloads) == 1


def test_reroute_is_not_used_for_other_refusals_or_non_submit_controls(monkeypatch):
    calls = engine_hooks(monkeypatch, {"state": "confirmed"})
    other = SubmitForm(hint="Use the submit action for final submission; it verifies fields")
    engine, _ = run_drive(monkeypatch, other, clicker)
    assert calls == [] and not [e for e in engine.events if e[0] == "submit_rerouted"]

    calls = engine_hooks(monkeypatch, {"state": "confirmed"})
    engine, _ = run_drive(monkeypatch, SubmitForm(button_type="button"), clicker)
    assert calls == []


def test_incomplete_page_returns_recovery_and_reroutes_only_once(monkeypatch):
    calls = engine_hooks(monkeypatch, {"recoverable": True, "reason": "Required field empty"})
    form = SubmitForm()

    def script(n, form):
        return (
            ApplicationDecision(action="click", control_id="b1")
            if n <= 3
            else ApplicationDecision(action="wait")
        )

    engine, _ = run_drive(monkeypatch, form, script)
    assert calls == [1]  # the second and third refused clicks are not rerouted again
    assert engine.submit_rerouted is True
