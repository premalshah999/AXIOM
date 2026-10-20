from unittest.mock import AsyncMock

import pytest

from jobpilot import inference_agent
from jobpilot.answers import Resolver
from jobpilot.inference_agent import Inference
from jobpilot.schemas import Answer, Profile


def question(label, options=("Yes", "No")):
    return {
        "id": "q",
        "label": label,
        "type": "radio",
        "required": True,
        "options": list(options),
        "value": "",
        "valid": True,
    }


@pytest.fixture
def resolver(service, monkeypatch):
    service.config.haiku_api_key = "test"
    monkeypatch.setattr(type(service.config), "llm_api_key", property(lambda self: "test"), raising=False)
    return Resolver(Profile(facts={"willing_to_relocate": True}), service.db, service.config)


async def test_supported_inference_answers_offered_option(resolver, monkeypatch):
    call = AsyncMock(
        return_value=Inference(
            answerable=True, value="Yes", confidence=0.9, evidence_ids=["fact:willing_to_relocate"]
        )
    )
    monkeypatch.setattr("jobpilot.models.structured", call)
    answer, outcome = await inference_agent.infer(
        resolver, question("Can you commit to a hybrid model from our LA office?")
    )
    assert outcome == "answered"
    assert answer.disposition == "answer" and answer.value == "Yes"
    assert answer.evidence_ids == ["fact:willing_to_relocate"]


@pytest.mark.parametrize(
    "result",
    [
        Inference(answerable=True, value="Yes", confidence=0.9, evidence_ids=["fact:invented"]),
        Inference(answerable=True, value="Maybe", confidence=0.9, evidence_ids=["fact:willing_to_relocate"]),
        Inference(answerable=True, value="Yes", confidence=0.4, evidence_ids=["fact:willing_to_relocate"]),
        Inference(answerable=False),
        # A near-match is not one of the offered options.
        Inference(
            answerable=True, value="Yes, I can", confidence=0.9, evidence_ids=["fact:willing_to_relocate"]
        ),
    ],
)
async def test_unsupported_inference_still_asks(resolver, monkeypatch, result):
    monkeypatch.setattr("jobpilot.models.structured", AsyncMock(return_value=result))
    answer, outcome = await inference_agent.infer(resolver, question("Can you work hybrid from LA?"))
    assert answer is None and outcome not in {"answered", "site_transient", "model_output"}


async def test_identity_questions_are_never_inferred(resolver, monkeypatch):
    call = AsyncMock()
    monkeypatch.setattr("jobpilot.models.structured", call)
    for label in [
        "What is your gender?",
        "Do you claim disability status?",
        "Last 4 digits of SSN",
        "Provide two professional references",
        "What is your current salary?",
    ]:
        assert await inference_agent.infer(resolver, question(label)) == (None, "ineligible")
    call.assert_not_awaited()


async def test_resolver_infers_before_parking(resolver, monkeypatch):
    parked = Answer(field_id="q", disposition="review", blocker_type="missing_fact", reason="missing")
    inferred = Answer(
        field_id="q", disposition="answer", value="Yes", evidence_ids=["fact:willing_to_relocate"]
    )
    monkeypatch.setattr(resolver, "_resolve", AsyncMock(return_value=[parked]))
    monkeypatch.setattr("jobpilot.inference_agent.infer", AsyncMock(return_value=(inferred, "answered")))
    answers = await resolver.resolve([question("Can you commit to a hybrid model?")])
    assert answers[0].disposition == "answer" and answers[0].value == "Yes"


async def test_provider_failure_is_not_a_missing_fact(resolver, monkeypatch):
    parked = Answer(field_id="q", disposition="review", blocker_type="missing_fact", reason="missing")
    monkeypatch.setattr(resolver, "_resolve", AsyncMock(return_value=[parked]))
    monkeypatch.setattr("jobpilot.models.structured", AsyncMock(side_effect=RuntimeError("HTTP 529")))
    answers = await resolver.resolve([question("Can you commit to a hybrid model?")])
    assert answers[0].disposition == "review"
    assert answers[0].blocker_type == "site_transient" and answers[0].retryable


async def test_other_employer_knowledge_never_enters_the_pack(resolver, service):
    from jobpilot.features.knowledge.tables import KnFact

    resolver.employer = "Acme"
    with service.db.session() as s:
        s.add(
            KnFact(
                id="other",
                key="kq1:why",
                category="motivation",
                question="Why Globex?",
                value={"v": "Globex mission"},
                layer="narrative",
                scope="employer",
                employer_id="emp:globex",
            )
        )
        s.add(
            KnFact(
                id="global",
                key="kq1:hybrid",
                category="work_preferences",
                question="Open to hybrid work?",
                value={"v": "Yes"},
                layer="confirmed",
                scope="global",
            )
        )
        s.add(
            KnFact(
                id="retired",
                key="kq1:old",
                category="work_preferences",
                question="Open to travel?",
                value={"v": "No"},
                layer="confirmed",
                scope="global",
                retired=True,
            )
        )
        s.commit()
    pack, _facts, evidence, _learned = inference_agent.evidence_pack(resolver, question("Why Acme?", ()))
    assert "knowledge:global" in pack and "knowledge:global" in evidence
    assert "knowledge:other" not in pack and "knowledge:retired" not in pack
