"""TypeSafe Jev answers confident pick-one questions before DeepSeek; anything else falls through."""

import json

import httpx

from jobpilot import jev
from jobpilot.answers import Resolver
from jobpilot.db import Budget
from jobpilot.schemas import Profile


def resolver(service, key="test-key", **facts):
    config = service.config.model_copy(update={"typesafe_api_key": key})
    return Resolver(Profile(facts={"willing_to_relocate": True, **facts}), service.db, config)


def field(label="Are you willing to relocate for this role?", options=("Yes", "No"), **extra):
    return {
        "id": "f1",
        "label": label,
        "type": "select",
        "required": True,
        "options": list(options),
        "value": "",
        "valid": True,
        **extra,
    }


def answer(choice, confidence=0.95, not_stated=0.01):
    return {
        "type": "choice",
        "choice": choice,
        "confidence": confidence,
        "probabilities": {choice: 1 - not_stated, jev.NOT_STATED: not_stated},
    }


def fake_decide(monkeypatch, reply):
    calls = []

    async def decide(db, config, run_id, purpose, state, questions, **kw):
        calls.append(questions)
        return {key: reply for key in questions} if reply is not None else None

    monkeypatch.setattr(jev, "decide", decide)
    return calls


async def test_confident_choice_is_kept_with_cited_facts(service, monkeypatch):
    calls = fake_decide(monkeypatch, answer("Yes"))
    kept = await resolver(service).jev_choices([field()])
    assert len(kept) == 1
    f, a = kept[0]
    assert a.value == "Yes" and a.disposition == "answer"
    assert "fact:willing_to_relocate" in a.evidence_ids
    question = next(iter(calls[0].values()))
    assert jev.NOT_STATED in question["criteria"] and question["type"] == "choice"
    # Only the question's own facts are sent, never the whole profile.
    assert set(question["instructions"]["applicant_facts"]) == {"willing_to_relocate"}


async def test_unsure_or_not_stated_falls_through(service, monkeypatch):
    fake_decide(monkeypatch, answer("Yes", confidence=0.6))
    assert await resolver(service).jev_choices([field()]) == []
    fake_decide(monkeypatch, answer(jev.NOT_STATED))
    assert await resolver(service).jev_choices([field()]) == []
    fake_decide(monkeypatch, answer("Yes", not_stated=0.2))
    assert await resolver(service).jev_choices([field()]) == []
    fake_decide(monkeypatch, None)  # provider failure
    assert await resolver(service).jev_choices([field()]) == []


async def test_free_text_partial_catalogs_and_unsupported_questions_are_not_sent(service, monkeypatch):
    calls = fake_decide(monkeypatch, answer("Yes"))
    r = resolver(service)
    assert await r.jev_choices([field(type="text", options=[])]) == []
    assert await r.jev_choices([field(options_partial=True)]) == []
    assert await r.jev_choices([field(label="Favourite colour?", options=["Red", "Blue"])]) == []
    assert calls == []


async def test_disabled_without_key_or_by_owner(service, monkeypatch):
    calls = fake_decide(monkeypatch, answer("Yes"))
    assert await resolver(service, key="").jev_choices([field()]) == []
    service.db.set_setting("jev", {"enabled": False})
    assert await resolver(service).jev_choices([field()]) == []
    assert calls == []


async def test_decide_meters_budget_and_never_logs_state(service, monkeypatch):
    seen = {}

    def handler(request):
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "model": "jev-1.13.0",
                "answers": {"q0": answer("No")},
                "usage": {"input_tokens": 1000, "output_tokens": 0},
            },
        )

    real = httpx.AsyncClient
    monkeypatch.setattr(
        jev.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw)
    )
    config = service.config.model_copy(update={"typesafe_api_key": "secret-key"})
    out = await jev.decide(
        service.db,
        config,
        None,
        "JevChoice",
        {"private": "state"},
        {"q0": jev.choice_question("Q?", ["Yes", "No"])},
    )
    assert out["q0"]["choice"] == "No"
    assert seen["auth"] == "Bearer secret-key" and seen["body"]["model"] == "jev-1.13.0"
    with service.db.session() as s:
        spent = sum(b.spent for b in s.query(Budget).all())
    assert abs(spent - 1000 * 0.042 / 1e6) < 1e-12
