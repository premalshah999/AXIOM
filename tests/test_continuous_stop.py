"""Stop-cause tagging, one cap extension, and the first-help refusal in continuous_agent.drive.

drive() runs end to end against a scripted fake engine/form; the model call
(conversation), the browser-facing observation helpers and the evidence helpers are
replaced, so no browser, database or model is needed.
"""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

from jobpilot import continuous_agent as ca
from jobpilot.continuous_agent import ApplicationDecision


def test_extra_steps_grants_one_window_only_when_recent_growth():
    history = [0] * 24 + [1]  # cap check at step 24, growth after step 12
    assert ca.extra_steps(history, 24, 24) == 12
    assert ca.extra_steps([0] * 25, 24, 24) == 0  # no growth
    assert ca.extra_steps([0] * 10 + [3] * 15, 24, 10) == 0  # before the cap: no extension
    assert ca.extra_steps([5], 0, 0) == 0  # too little history


def test_help_allowed_refuses_first_help_only_in_young_run_with_open_required_fields():
    assert ca.help_allowed(0, 2, False) is False  # first help, young, open fields: refused
    assert ca.help_allowed(0, 2, True) is True  # already refused once: second help ends
    assert ca.help_allowed(3, 2, False) is False
    assert ca.help_allowed(4, 2, False) is True  # 4 steps run: allowed
    assert ca.help_allowed(6, 1, False) is True  # after 6 steps: allowed
    assert ca.help_allowed(0, 0, False) is True  # nothing required is open: allowed


def test_unfilled_required_counts_empty_and_invalid_required_fields():
    obs = {
        "fields": [
            {"id": "a", "required": True, "type": "text", "value": "", "valid": True},
            {"id": "b", "required": True, "type": "text", "value": "x", "valid": False},
            {"id": "c", "required": True, "type": "file", "value": ""},
            {"id": "d", "required": False, "type": "text", "value": ""},
            {"id": "e", "required": True, "type": "text", "value": "ok", "valid": True},
        ]
    }
    assert ca.unfilled_required(obs) == 2


class FakeForm:
    def __init__(self, required_open=True):
        self.resolver = SimpleNamespace(resolve=None)
        self.failed_strategies = {}
        self.buttons = {}
        self.fields = {}
        self.verified = 0
        self.fill_calls = 0
        self.obs = {
            "fields": [
                {
                    "id": "f1",
                    "label": "Phone",
                    "type": "text",
                    "required": True,
                    "value": "" if required_open else "555",
                    "valid": True,
                }
            ],
            "progress_fingerprint": "fp-0",
            "text": "Apply to this job",
            "headings": [],
            "controls": [],
            "revision": 1,
            "owner_id": "owner",
            "busy": False,
        }

    async def scan(self):
        return dict(self.obs)

    async def settle(self, timeout=None):
        return None

    async def fill_current(self):
        self.fill_calls += 1
        return {"ok": True, "verified_fields": self.verified, "pending": []}


class FakeEngine:
    def __init__(self, form):
        self.form = form
        self.result = None
        self.armed = False
        self.db = SimpleNamespace(get_setting=lambda name, default=None: default)
        self.job = {"company": "Acme", "title": "Analyst", "description": "Role"}
        self.run_record = {"packet": {"runner_tier": 1}}
        self.events = []

    def emit(self, kind, message, data=None):
        self.events.append((kind, message, data))

    async def browser_challenge(self):
        return None

    async def handle_email(self):
        return None

    async def handle_account_recovery(self):
        return False

    async def accept_policy_dialog(self, obs):
        return False

    async def recover_empty_observation(self, obs):
        return obs

    async def checkpoint_draft(self):
        return None

    def controller_blocker(self, reason="", *, original=None):
        return {"state": "needs_review", "reason": reason}

    def record_help_reviews(self, answers):
        return None


def run_drive(monkeypatch, form, script):
    """Run drive() with decisions from script(call_number, form); return the payloads seen."""
    import jobpilot.browser as browser

    monkeypatch.setattr(browser, "navigation_observation", lambda obs: {})
    monkeypatch.setattr(ca, "evidence_context", lambda resolver: ({}, {}, {}))
    monkeypatch.setattr(ca, "navigation_context", lambda form, obs: {})

    async def history(form):
        return []

    monkeypatch.setattr(ca, "history_observation", history)
    engine = FakeEngine(form)
    payloads = []

    @asynccontextmanager
    async def fake_conversation(eng):
        async def decide(payload):
            payloads.append(payload)
            return script(len(payloads), form)

        yield decide

    monkeypatch.setattr(ca, "conversation", fake_conversation)
    asyncio.run(ca.drive(engine))
    return engine, payloads


def stop_events(engine):
    return [e for e in engine.events if e[0] == "continuous_stop"]


def test_frozen_fingerprint_stops_as_unchanged_8_within_8_decisions(monkeypatch):
    form = FakeForm()

    def script(n, form):
        return ApplicationDecision(action="wait")

    engine, payloads = run_drive(monkeypatch, form, script)
    assert len(payloads) <= 8
    assert engine.result["stop_cause"] == "unchanged_8"
    assert stop_events(engine)[-1][1] == "unchanged_8"


def test_repeated_refused_strategy_stops_as_repeat_refused(monkeypatch):
    form = FakeForm()

    def script(n, form):
        return ApplicationDecision(action="fill")

    engine, payloads = run_drive(monkeypatch, form, script)
    # Calls 1-2 execute, call 3 is refused without executing, call 4 breaks.
    assert form.fill_calls == 2
    assert len(payloads) == 4
    assert engine.result["stop_cause"] == "repeat_refused"


def test_step_cap_without_growth_is_not_extended(monkeypatch):
    form = FakeForm()

    def script(n, form):
        form.obs["progress_fingerprint"] = f"fp-{n}"
        return ApplicationDecision(action="fill")

    engine, payloads = run_drive(monkeypatch, form, script)
    assert len(payloads) == 24
    assert engine.result["stop_cause"] == "step_cap"


def test_growth_every_6_steps_extends_cap_exactly_once(monkeypatch):
    form = FakeForm()

    def script(n, form):
        # Verified fields grow by one every 6 steps for 40 steps.
        form.verified = min(n, 40) // 6
        form.obs["progress_fingerprint"] = f"fp-{n}"
        return ApplicationDecision(action="fill")

    engine, payloads = run_drive(monkeypatch, form, script)
    # Cap 24 is reached with growth in the last 12 steps, so 12 extra steps run once.
    # The second cap check (36) is not extended again.
    assert len(payloads) == 36
    assert engine.result["stop_cause"] == "step_cap"
    assert stop_events(engine)[-1][2]["verified_fields"] == 36 // 6


def test_first_help_with_required_fields_open_is_refused_once(monkeypatch):
    form = FakeForm(required_open=True)

    def script(n, form):
        form.obs["progress_fingerprint"] = f"fp-{n}"
        return ApplicationDecision(action="help", reason="stuck")

    engine, payloads = run_drive(monkeypatch, form, script)
    assert len(payloads) == 2
    assert payloads[1]["last_result"]["reason"].startswith("Help refused")
    assert engine.result["state"] == "needs_review"
    assert stop_events(engine)[-1][1] == "help"


def test_help_after_six_steps_is_allowed(monkeypatch):
    form = FakeForm(required_open=True)

    def script(n, form):
        form.obs["progress_fingerprint"] = f"fp-{n}"
        if n <= 6:
            return ApplicationDecision(action="wait")
        return ApplicationDecision(action="help", reason="blocked")

    engine, payloads = run_drive(monkeypatch, form, script)
    assert len(payloads) == 7
    assert engine.result["reason"] == "blocked"
    assert stop_events(engine)[-1][1] == "help"
