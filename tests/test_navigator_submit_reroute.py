"""A plain click on the real submit control is routed through the verified submit path once.

Toast (Greenhouse): the form was complete and verified, the model clicked "Apply now!" as an ordinary click, the guard
said "use submit_application", and the run looped on another control. run_desktop_navigator routes that one click to
submit_step(), where finish() still refuses an incomplete page. The scenarios drive the real navigator loop against a
scripted fake form and model; no browser is needed.
"""

import asyncio
from types import SimpleNamespace

import pytest

from jobpilot import continuous_agent, models
from jobpilot import browser as browser_module
from jobpilot.browser import BrowserEngine, NavigationDecision

HINT = "This may submit the application; use submit_application"


class Form:
    def __init__(self, label="Submit application", button_type="submit", hint=HINT, prepared=()):
        self.buttons = {"b1": {"id": "b1", "label": label, "type": button_type}}
        self.fields = {}
        self.failed_strategies = {}
        self.openers = None
        self.page = None
        self.resolver = None
        self.hint = hint
        self.prepared = prepared
        self.clicks = 0
        self.last_action = SimpleNamespace(model_dump=lambda: {"executed": True})

    async def scan(self):
        return {
            "url": "https://jobs.example.test/apply/1",
            "headings": ["Review"],
            "errors": [],
            "text": "Review your application",
            "fields": [],
            "controls": [dict(b) for b in self.buttons.values()],
            "options": [],
            "revision": 1,
            "owner_id": "owner",
            "busy": False,
            "progress_fingerprint": "fp-" + str(self.clicks),
        }

    async def click(self, control_id, **kwargs):
        self.clicks += 1
        raise ValueError(self.hint)

    async def application_submit_controls(self):
        return [self.buttons[i] for i in self.prepared]

    def stuck_fields(self):
        return []

    async def settle(self, timeout=None):
        return None


class Engine:
    """Just enough of BrowserEngine for run_desktop_navigator: every method the loop calls does nothing."""

    reroute_submit_hint = "use submit_application"
    fallback_mail_adapter = None
    auto_submitted = False

    def __init__(self, form, service, final, armed=False, rerouted=False):
        self.form, self.service, self.final = form, service, final
        self.db, self.config = service.db, service.config
        self.armed, self.submit_rerouted, self.result = armed, rerouted, None
        self.run_id, self.page = "run-a", None
        self.run_record = {"packet": {}, "mode": "dry_run"}
        self.job = {"id": "j1", "company": "ExampleSoft", "title": "Analyst", "ats": "custom", "url": "https://jobs.example.test/1"}
        self.events, self.submits = [], 0

        async def before_click(control_id):
            return None

        self.email_verification = SimpleNamespace(before_click=before_click)

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

    def successfactors_application_step(self, obs):
        return False

    def answer_verdict(self, decision, target, feedback, rejected):
        return None

    def recovery_exhausted_reason(self, *args, **kwargs):
        return "exhausted"

    def controller_blocker(self, reason="", **kwargs):
        return {"state": "needs_review", "reason": str(reason)}

    def stuck_control_result(self, target, failures):
        return {"state": "needs_review", "reason": "stuck"}

    async def recover_empty_observation(self, obs):
        return obs

    async def await_successfactors_handoff(self, obs):
        return obs

    async def submit_step(self):
        self.submits += 1
        self.result = self.final
        return "{}", False


def drive(monkeypatch, engine, decisions=2):
    asked = []

    async def structured(*args, **kwargs):
        asked.append(1)
        return NavigationDecision(action="click", control_id="b1")

    monkeypatch.setattr(models, "structured", structured)
    monkeypatch.setattr(models, "effective_thinking", lambda *a: "disabled")
    monkeypatch.setattr(continuous_agent, "navigation_context", lambda form, obs: {})
    monkeypatch.setattr(browser_module, "navigation_supported_answers", lambda form, obs: {})
    asyncio.run(BrowserEngine.run_desktop_navigator(engine))
    return asked


def rerouted(engine):
    return [e for e in engine.events if e[0] == "submit_rerouted"]


def test_refused_plain_click_on_a_submit_control_runs_the_verified_submit(service, monkeypatch):
    engine = Engine(Form(), service, {"state": "confirmed", "reason": "done"})
    drive(monkeypatch, engine)
    assert engine.submits == 1 and engine.form.clicks == 1
    assert engine.result == {"state": "confirmed", "reason": "done"}
    assert engine.submit_rerouted is True
    (event,) = rerouted(engine)
    assert event[2]["control_id"] == "b1"


def test_a_prepared_submit_control_is_routed_even_when_its_label_is_not_a_submit_word(service, monkeypatch):
    # SuccessFactors' single "Apply" and ASSA ABLOY's "Apply now!": the form names it as its prepared submit.
    engine = Engine(Form(label="Apply now!", prepared=("b1",)), service, {"state": "confirmed"})
    drive(monkeypatch, engine)
    assert engine.submits == 1 and len(rerouted(engine)) == 1


@pytest.mark.parametrize(
    "form, armed, already",
    [
        (Form(label="Edit"), False, False),  # "Edit" is also type=submit (SAIC): not the final submit
        (Form(hint="Use the submit action for final submission; it verifies fields"), False, False),
        (Form(button_type="button"), False, False),
        (Form(), True, False),  # an armed submission is never rerouted
        (Form(), False, True),  # one reroute per run
    ],
)
def test_the_reroute_is_not_used_for_other_refusals_or_controls(service, monkeypatch, form, armed, already):
    engine = Engine(form, service, {"state": "confirmed"}, armed=armed, rerouted=already)
    drive(monkeypatch, engine)
    assert engine.submits == 0 and not rerouted(engine)
    assert form.clicks >= 1
