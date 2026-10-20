"""Refusal visibility: the model sees why a finish/submit was refused, and a repeat refusal says why.

recovery_observation and repeat_refusal are pure. The drive() test reuses the scripted
fake engine/form pattern from test_continuous_stop.py; no browser, database or model is needed.
"""

import asyncio
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

from jobpilot import continuous_agent as ca
from jobpilot.browser import recovery_observation
from jobpilot.continuous_agent import ApplicationDecision


LANGUAGE_REFUSAL = "Needs an approved answer: Language"


def language_candidate(**pending_extra):
    pending = {
        "question": "Language",
        "field_id": "f1",
        "reason": "Model omitted this question",
        "blocker_type": "missing_fact",
        "required": True,
        "retryable": False,
        "options": ["English", "Spanish"],
        "supporting_facts": [],
        "type": "dropdown",
    }
    pending.update(pending_extra)
    return {
        "state": "needs_review",
        "reason": LANGUAGE_REFUSAL,
        "blocker_type": "missing_fact",
        "recovery_cause": None,
        "reviews": [],
        "report": {
            "ok": False,
            "problems": [LANGUAGE_REFUSAL],
            "pending": [pending],
            "verified_fields": 77,
            "resume_attached": True,
        },
    }


def test_recovery_observation_keeps_finish_reason_and_pending_reason():
    obs = recovery_observation(language_candidate())
    assert obs["reason"] == LANGUAGE_REFUSAL
    assert obs["problems"] == [LANGUAGE_REFUSAL]
    assert obs["fields"][0]["question"] == "Language"
    assert obs["fields"][0]["reason"] == "Model omitted this question"
    assert obs["fields"][0]["required"] is True
    assert obs["prerequisites"]["verified_fields"] == 77


def test_recovery_observation_forwards_offered_options_and_supporting_facts():
    obs = recovery_observation(language_candidate(supporting_facts=["fact:languages"]))
    assert obs["fields"][0]["options"] == ["English", "Spanish"]
    assert obs["fields"][0]["supporting_facts"] == ["fact:languages"]
    empty = recovery_observation(language_candidate())
    assert empty["fields"][0]["supporting_facts"] == []


def test_recovery_observation_bounds_text_and_removes_secrets():
    typed = "typed-value-xyz"
    long_reason = "Needs an approved answer: Language " + "x" * 1000
    problems = [f"Required field incomplete: Question {i}" for i in range(20)]
    problems[0] = "Value not accepted: Contact jane.doe@example.com phone (410) 555-0199"
    problems[1] = "Entered field is invalid: password: hunter22"
    problems[2] = "Resume attachment was not accepted at https://portal.example.com/apply?token=abc"
    problems[3] = "Code 482913 was rejected"
    candidate = {
        "state": "needs_review",
        "reason": long_reason,
        "report": {
            "problems": problems,
            "pending": [
                {
                    "question": "Phone " + "y" * 500,
                    "reason": "Call 410-555-0199 or jane.doe@example.com",
                    "value": typed,
                    "options": [f"Option {i} " + "z" * 300 for i in range(20)],
                    "supporting_facts": [
                        "fact:phone 410-555-0199",
                        "fact:x" + "w" * 500,
                        "fact:b",
                        "fact:c",
                        "fact:d",
                        "fact:e",
                        "fact:f",
                        "fact:g",
                    ],
                    "required": True,
                }
            ],
        },
    }
    obs = recovery_observation(candidate)
    dumped = json.dumps(obs)

    assert len(obs["reason"]) <= 200
    assert len(obs["problems"]) == 8
    assert all(len(p) <= 200 for p in obs["problems"])
    item = obs["fields"][0]
    assert len(item["question"]) <= 200 and len(item["reason"]) <= 200
    assert len(item["options"]) == 12 and all(len(o) <= 120 for o in item["options"])
    assert len(item["supporting_facts"]) == 6 and all(len(f) <= 200 for f in item["supporting_facts"])

    for secret in (
        typed,
        "jane.doe@example.com",
        "555-0199",
        "410",
        "hunter22",
        "482913",
        "https://",
        "token=abc",
    ):
        assert secret not in dumped, secret
    assert "[number omitted]" in dumped


def test_recovery_observation_tolerates_missing_report_and_reviews():
    obs = recovery_observation({"reason": "Employer sign-in must complete", "report": None})
    assert obs["reason"] == "Employer sign-in must complete"
    assert obs["problems"] == [] and obs["fields"] == []
    assert recovery_observation({})["reason"] == ""


def test_repeat_refusal_restates_last_failure_and_names_the_control():
    memory = [
        {"action": "click", "control_id": "b9", "detail": "Button moved", "problems": []},
        {
            "action": "submit",
            "control_id": "",
            "detail": LANGUAGE_REFUSAL,
            "problems": [LANGUAGE_REFUSAL],
            "failure_category": None,
        },
    ]
    text = ca.repeat_refusal("submit", "", "", memory)
    assert text.startswith("Unchanged strategy already failed")
    assert LANGUAGE_REFUSAL in text
    assert "Button moved" not in text
    assert "submit" in text and "different strategy" in text and "help" in text


def test_repeat_refusal_names_the_repeated_field_label():
    memory = [
        {
            "action": "choose",
            "control_id": "f7",
            "detail": "No matching option is offered",
            "problems": [],
            "failure_category": "unsupported_choice",
        }
    ]
    text = ca.repeat_refusal("choose", "f7", "How Did You Hear About Us?", memory)
    assert "'How Did You Hear About Us?'" in text
    assert "No matching option is offered" in text


def test_repeat_refusal_without_recorded_failure_still_says_what_to_do():
    text = ca.repeat_refusal("fill", "f9", "Phone", [])
    assert text.startswith("Unchanged strategy already failed")
    assert "'Phone'" in text and "Choose another strategy" in text


class FakeForm:
    def __init__(self):
        self.resolver = SimpleNamespace(resolve=None)
        self.failed_strategies = {}
        self.buttons = {}
        self.fields = {}
        self.obs = {
            "fields": [
                {
                    "id": "f1",
                    "label": "Language",
                    "type": "dropdown",
                    "required": True,
                    "value": "",
                    "valid": True,
                }
            ],
            "progress_fingerprint": "fp-0",
            "text": "Review your application",
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


class FakeEngine:
    def __init__(self, form, candidate):
        self.form = form
        self.candidate = candidate
        self.result = None
        self.armed = False
        self.finish_calls = 0
        self.db = SimpleNamespace(get_setting=lambda name, default=None: default)
        self.job = {"company": "Vizient", "title": "Analyst", "description": "Role"}
        self.run_record = {"packet": {"runner_tier": 1}}
        self.events = []

    def emit(self, kind, message, data=None):
        self.events.append((kind, message, data))

    async def finish(self):
        self.finish_calls += 1
        return self.candidate

    def recoverable_result(self, result):
        return True

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


def test_repeated_submit_shows_refusal_reason_and_repeat_text_to_model(monkeypatch):
    import jobpilot.browser as browser

    monkeypatch.setattr(browser, "navigation_observation", lambda obs: {})
    monkeypatch.setattr(ca, "evidence_context", lambda resolver: ({}, {}, {}))
    monkeypatch.setattr(ca, "navigation_context", lambda form, obs: {})

    async def history(form):
        return []

    monkeypatch.setattr(ca, "history_observation", history)
    form = FakeForm()
    engine = FakeEngine(form, language_candidate())
    payloads = []

    @asynccontextmanager
    async def fake_conversation(eng):
        async def decide(payload):
            payloads.append(payload)
            return ApplicationDecision(action="submit")

        yield decide

    monkeypatch.setattr(ca, "conversation", fake_conversation)
    asyncio.run(ca.drive(engine))

    # Calls 1-2 submit, call 3 is refused without a finish, call 4 breaks the loop.
    assert engine.finish_calls == 2
    assert len(payloads) == 4
    first = payloads[1]["last_result"]
    assert first["reason"] == LANGUAGE_REFUSAL
    assert first["fields"][0]["reason"] == "Model omitted this question"
    assert first["fields"][0]["options"] == ["English", "Spanish"]
    refused = payloads[3]["last_result"]["reason"]
    assert refused.startswith("Unchanged strategy already failed")
    assert LANGUAGE_REFUSAL in refused
    assert engine.result["stop_cause"] == "repeat_refused"
    assert "typed-value-xyz" not in json.dumps(payloads)
