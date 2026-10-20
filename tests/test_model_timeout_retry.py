import httpx
import pytest
from sqlalchemy import func, select

from jobpilot import models
from jobpilot.db import Event


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
