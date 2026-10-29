"""Identical requests inside one run are paid once; an optional vision call cannot park the provider (2026-10-28)."""

import httpx
import pytest
from sqlalchemy import func, select

from jobpilot import models, provider_health
from jobpilot.db import Event
from jobpilot.inference_agent import Inference
from jobpilot.schemas import AnswerBatch


@pytest.fixture(autouse=True)
def fresh_reuse():
    models._reuse.clear()
    yield
    models._reuse.clear()


def reuse_events(service):
    with service.db.session() as s:
        return s.scalar(select(func.count()).select_from(Event).where(Event.kind == "model_reuse"))


async def test_identical_request_in_a_run_is_answered_once(service, monkeypatch):
    calls = []

    async def call(*args, **kwargs):
        calls.append(args)
        return Inference(answerable=False, rationale="fixture")

    monkeypatch.setattr(models, "_structured", call)
    args = (service.config, service.db, Inference, "rules", '{"q": 1}', "run-a")
    first = await models.structured(*args)
    second = await models.structured(*args)
    assert len(calls) == 1 and first == second and first is not second
    assert reuse_events(service) == 1
    second.rationale = "mutated"
    assert (await models.structured(*args)).rationale == "fixture"


async def test_changed_prompt_or_run_or_purpose_is_not_reused(service, monkeypatch):
    calls = []

    async def call(*args, **kwargs):
        calls.append(args)
        return Inference(answerable=False)

    monkeypatch.setattr(models, "_structured", call)
    base = [service.config, service.db, Inference, "rules", '{"q": 1}', "run-a"]
    await models.structured(*base)
    await models.structured(*base[:4], '{"q": 2}', "run-a")  # the facts or the question changed
    await models.structured(*base[:5], "run-b")  # another application
    await models.structured(*base[:2], AnswerBatch, *base[3:])  # another purpose
    assert len(calls) == 4


async def test_failures_and_decision_loops_are_never_reused(service, monkeypatch):
    class NavigationDecision(Inference):
        pass

    outcomes = [models.AnswerModelUnavailable(), NavigationDecision(answerable=True), NavigationDecision(answerable=True)]
    calls = []

    async def call(*args, **kwargs):
        calls.append(1)
        outcome = outcomes[len(calls) - 1]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(models, "_structured", call)
    args = (service.config, service.db, Inference, "rules", "{}", "run-a")
    with pytest.raises(models.AnswerModelUnavailable):
        await models.structured(*args)
    # The failure left nothing behind, so the retry is a real request.
    await models.structured(*args[:2], NavigationDecision, *args[3:])
    await models.structured(*args[:2], NavigationDecision, *args[3:])
    assert len(calls) == 3


async def test_reuse_is_bounded_per_run(service, monkeypatch):
    async def call(*args, **kwargs):
        return Inference(answerable=False)

    monkeypatch.setattr(models, "_structured", call)
    for i in range(models.REUSE_PER_RUN + 10):
        await models.structured(service.config, service.db, Inference, "rules", str(i), "run-a")
    assert sum(1 for k in models._reuse if k[0] == "run-a") <= models.REUSE_PER_RUN


async def test_vision_rejection_does_not_open_the_provider_cooldown(service):
    config = service.config.model_copy(update={"llm_provider": "deepseek"})

    async def respond(request):
        return httpx.Response(404, json={"error": {"message": "no endpoint supports image input"}})

    for purpose in ("browser_vision",):
        transport = models.MeteredTransport(
            service.db, config, "run-a", inner=httpx.MockTransport(respond), thinking="disabled", purpose=purpose
        )
        async with httpx.AsyncClient(transport=transport, base_url="https://api.example.test/v1") as client:
            for _ in range(3):
                with pytest.raises(models.AnswerModelUnavailable):
                    await client.post("/chat/completions", json={"model": "fixture", "messages": []})
    assert "retry_at" not in (service.db.get_setting(provider_health.key(config)) or {})


async def test_the_same_rejection_on_an_answer_call_still_opens_it(service):
    config = service.config.model_copy(update={"llm_provider": "deepseek"})

    async def respond(request):
        return httpx.Response(500)

    transport = models.MeteredTransport(
        service.db, config, inner=httpx.MockTransport(respond), thinking="disabled", purpose="AnswerBatch"
    )
    async with httpx.AsyncClient(transport=transport, base_url="https://api.example.test/v1") as client:
        for _ in range(2):
            with pytest.raises(models.AnswerModelUnavailable):
                await client.post("/chat/completions", json={"model": "fixture", "messages": []})
    assert provider_health.acquire(service.db, config, 25) is None
