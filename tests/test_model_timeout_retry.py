import asyncio
import json
import time

import httpx
import pytest
from sqlalchemy import func, select

from jobpilot import models
from jobpilot import provider_health
from jobpilot.db import Event


@pytest.fixture(autouse=True)
def isolated_retries(monkeypatch):
    models._retry_streak.clear()
    monkeypatch.setattr(models, "CONNECTION_BACKOFF", 0.0)
    yield
    models._retry_streak.clear()


async def test_timeout_retries_model_only_and_preserves_result(service, monkeypatch):
    calls = []

    async def call(*args, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise models.AnswerModelTimeout()
        return "decision"

    monkeypatch.setattr(models, "_structured", call)
    assert await models.structured(service.config, service.db, run_id="fixture") == "decision"
    assert len(calls) == 2


async def test_timeout_retry_budget_is_shared_across_calls(service, monkeypatch):
    calls = []

    async def call(*args, **kwargs):
        calls.append(1)
        raise models.AnswerModelTimeout()

    monkeypatch.setattr(models, "_structured", call)
    for _ in range(3):
        with pytest.raises(models.AnswerModelTimeout):
            await models.structured(service.config, service.db, run_id="fixture")
    assert len(calls) == 5
    with service.db.session() as session:
        assert session.scalar(select(func.count()).select_from(Event).where(
            Event.kind == "model_timeout_retry"
        )) == 2


@pytest.mark.parametrize("failure", [models.AnswerModelUnavailable, models.BudgetExceeded, ValueError])
async def test_non_timeout_failure_never_retries(service, monkeypatch, failure):
    calls = []

    async def call(*args, **kwargs):
        calls.append(1)
        raise failure()

    monkeypatch.setattr(models, "_structured", call)
    with pytest.raises(failure):
        await models.structured(service.config, service.db)
    assert len(calls) == 1


async def test_transport_timeout_type_survives_sdk_wrapping(service):
    async def fail(request):
        raise httpx.ReadTimeout("fixture")

    transport = models.MeteredTransport(service.db, service.config, inner=httpx.MockTransport(fail))
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(models.AnswerModelTimeout):
            async with models.model_deadline(1):
                await client.post("https://example.test/chat/completions", json={"messages": []})


async def test_connection_failure_keeps_retryable_type_through_deadline(service):
    async def fail(request):
        raise httpx.ConnectError("fixture")
    transport = models.MeteredTransport(service.db, service.config, inner=httpx.MockTransport(fail))
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(models.AnswerModelConnectionError):
            async with models.model_deadline(1):
                await client.post("https://example.test/chat/completions", json={"messages": []})


async def test_connection_and_timeout_share_existing_retry_budget(service, monkeypatch):
    calls = []
    failures = [models.AnswerModelConnectionError, models.AnswerModelTimeout,
                models.AnswerModelConnectionError, models.AnswerModelTimeout,
                models.AnswerModelConnectionError]
    async def call(*args, **kwargs):
        failure = failures[len(calls)]
        calls.append(failure)
        raise failure()
    monkeypatch.setattr(models, "_structured", call)
    for _ in range(3):
        with pytest.raises(models.AnswerModelUnavailable):
            await models.structured(service.config, service.db, run_id="fixture")
    assert len(calls) == 5
    with service.db.session() as session:
        assert session.scalar(select(func.count()).select_from(Event).where(
            Event.run_id == "fixture", Event.kind == "model_timeout_retry"
        )) == 2


async def test_isolated_failures_between_successes_never_end_the_run(service, monkeypatch):
    """Two parallel batches timing out used the run's whole budget of two, so the next lone timeout ended it although
    every call around it worked (greenhouse/lever/workday/oracle, 2026-10-31)."""
    outcomes = iter(["fail", "fail", "ok", "ok", "fail", "ok", "fail", "ok"])
    calls = []

    async def call(*args, **kwargs):
        calls.append(1)
        if next(outcomes) == "fail":
            raise models.AnswerModelTimeout()
        return "decision"

    monkeypatch.setattr(models, "_structured", call)
    first, second = await asyncio.gather(
        models.structured(service.config, service.db, run_id="fixture"),
        models.structured(service.config, service.db, run_id="fixture"),
    )
    assert (first, second) == ("decision", "decision")
    assert await models.structured(service.config, service.db, run_id="fixture") == "decision"
    assert await models.structured(service.config, service.db, run_id="fixture") == "decision"
    assert len(calls) == 8


async def test_a_run_with_only_failures_still_stops_and_the_total_is_bounded(service, monkeypatch):
    calls = []

    async def call(*args, **kwargs):
        calls.append(1)
        raise models.AnswerModelTimeout()

    monkeypatch.setattr(models, "_structured", call)
    for _ in range(4):
        with pytest.raises(models.AnswerModelTimeout):
            await models.structured(service.config, service.db, run_id="fixture")
    assert len(calls) <= 4 + models.RETRY_STREAK  # no success in between: the streak limit holds
    models._retry_streak.clear()
    with service.db.exclusive() as session:
        for _ in range(models.RETRY_TOTAL - 2):
            session.add(Event(run_id="fixture", kind="model_timeout_retry", message="x", data={}))
    before = len(calls)
    with pytest.raises(models.AnswerModelTimeout):
        await models.structured(service.config, service.db, run_id="fixture")
    assert len(calls) == before + 1  # the run's total is spent: no retry at all


async def test_a_retry_asks_the_next_host(service, monkeypatch):
    seen = []

    async def call(*args, **kwargs):
        seen.append(models._host_attempt.get())
        if len(seen) < 3:
            raise models.AnswerModelTimeout()
        return "decision"

    monkeypatch.setattr(models, "_structured", call)
    assert await models.structured(service.config, service.db, run_id="fixture") == "decision"
    assert seen == [0, 1, 2]
    assert models._host_attempt.get() == 0


async def test_retry_host_order_rotates_and_first_try_is_unchanged(service):
    config = service.config.model_copy(update={"llm_provider": "openrouter", "openrouter_api_key": "fixture-only"})
    orders = []

    async def respond(request):
        orders.append(json.loads(request.content)["provider"]["order"])
        raise httpx.ConnectError("fixture")

    transport = models.MeteredTransport(service.db, config, inner=httpx.MockTransport(respond))
    async with httpx.AsyncClient(transport=transport, base_url="https://openrouter.ai/api/v1") as client:
        for attempt in (0, 1):
            token = models._host_attempt.set(attempt)
            try:
                with pytest.raises(models.AnswerModelUnavailable):
                    await client.post("/chat/completions", json={"model": "fixture", "messages": []})
            finally:
                models._host_attempt.reset(token)
    assert orders[0][0] == "sail-research" and orders[1][0] == "deepinfra"
    assert sorted(orders[0]) == sorted(orders[1])


async def test_a_refused_connection_opens_only_a_short_cooldown_for_everyone(service):
    config = service.config
    for _ in range(2):
        provider_health.finish(service.db, config, provider_health.acquire(service.db, config, 25), failure="transport_error")
    state = service.db.get_setting(provider_health.key(config))
    assert provider_health.acquire(service.db, config, 25) is None
    assert state["retry_at"] - time.time() <= provider_health.TRANSPORT_COOLDOWN_SECONDS + 1
    state["retry_at"] = time.time() - 1
    service.db.set_setting(provider_health.key(config), state)
    first, second = provider_health.acquire(service.db, config, 25), provider_health.acquire(service.db, config, 25)
    assert first is not None and second is not None  # no one-request recovery lease: waiting runs all proceed
