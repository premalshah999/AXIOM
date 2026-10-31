"""Refusal visibility: the model sees why a finish/submit was refused.

recovery_observation is pure; no browser, database or model is needed.
"""

import json

from jobpilot.browser import recovery_observation


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
