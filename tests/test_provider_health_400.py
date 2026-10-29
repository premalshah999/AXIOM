"""HTTP 400 is a request problem, not a provider outage; a short cooldown is waited out."""

import asyncio
import time

import httpx
import pytest
from pydantic import BaseModel

from jobpilot import models, provider_health
from jobpilot.agent_settings import current, save


def test_plain_400_is_client_error_not_outage():
    result = models.provider_rejection(httpx.Response(400), b'{"error":{"message":"invalid request"}}')
    assert result == {"rejection_category": "client_error", "retry_after_seconds": None}


def test_400_with_credit_exhaustion_still_counts_as_quota():
    body = b'{"type":"error","error":{"type":"invalid_request_error","message":"Your credit balance is too low to access the API"}}'
    result = models.provider_rejection(httpx.Response(400), body)
    assert result == {"rejection_category": "quota_exhausted", "retry_after_seconds": None}


def test_429_classification_unchanged():
    assert models.provider_rejection(httpx.Response(429), b"{}") == {
        "rejection_category": "rate_limited",
        "retry_after_seconds": None,
    }


def test_repeated_400_does_not_put_role_into_cooldown(service):
    ticket = provider_health.acquire(service.db, service.config, 25)
    for _ in range(3):
        provider_health.finish(service.db, service.config, ticket, failure="client_error", retry_after=None)
    assert provider_health.acquire(service.db, service.config, 25) is not None
    assert "retry_at" not in (service.db.get_setting(provider_health.key(service.config)) or {})


async def test_metered_400_responses_do_not_trip_cooldown(service):
    config = service.config.model_copy(update={"llm_provider": "deepseek"})
    calls = []

    async def respond(request):
        calls.append(request)
        return httpx.Response(400, json={"error": {"message": "invalid request"}})

    transport = models.MeteredTransport(
        service.db, config, inner=httpx.MockTransport(respond), thinking="disabled"
    )
    async with httpx.AsyncClient(transport=transport, base_url="https://api.example.test/v1") as client:
        for _ in range(2):
            with pytest.raises(models.AnswerModelUnavailable):
                await client.post("/chat/completions", json={"model": "fixture", "messages": []})
    assert len(calls) == 2
    assert provider_health.acquire(service.db, config, 25) is not None


async def test_timeout_before_any_request_is_not_provider_unavailable():
    with pytest.raises(TimeoutError):
        async with models.model_deadline(0.05):
            await asyncio.sleep(1)


async def test_timeout_after_request_was_sent_is_provider_unavailable():
    with pytest.raises(models.AnswerModelUnavailable):
        async with models.model_deadline(0.05):
            models._request_sent.get()["sent"] = True
            await asyncio.sleep(1)


async def test_short_cooldown_is_waited_out_and_long_one_is_not(service):
    config = service.config
    provider_health.finish(
        service.db,
        config,
        provider_health.acquire(service.db, config, 25),
        failure="rate_limited",
        retry_after=1,
    )
    started = time.monotonic()
    await provider_health.wait_out_short_cooldown(service.db, config)
    assert time.monotonic() - started >= 0.9
    recovery = provider_health.acquire(service.db, config, 25)
    assert recovery is not None
    provider_health.finish(service.db, config, recovery)

    provider_health.finish(
        service.db,
        config,
        provider_health.acquire(service.db, config, 25),
        failure="rate_limited",
        retry_after=3600,
    )
    started = time.monotonic()
    await provider_health.wait_out_short_cooldown(service.db, config)
    assert time.monotonic() - started < 30  # a long cooldown (3600 s here) returns at once; the margin is for a loaded machine
    assert provider_health.acquire(service.db, config, 25) is None


async def test_structured_call_rides_out_short_429_cooldown(service, monkeypatch):
    service.config.haiku_api_key = "fixture-only"
    value = current(service.db, service.config).model_dump()
    value["models"]["answers"] = "haiku"
    save(service.db, service.config, value)
    responses = []

    async def respond(request):
        responses.append(request.url.path)
        if len(responses) == 1:
            return httpx.Response(429, headers={"retry-after": "1"}, json={"error": {"message": "slow down"}})
        return httpx.Response(
            200,
            json={
                "id": "fixture",
                "type": "message",
                "role": "assistant",
                "model": "claude-haiku-5-5",
                "content": [{"type": "text", "text": '{"ok":true}'}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 10, "output_tokens": 5},
            },
        )

    def client(db, config, run_id=None, **kwargs):
        return httpx.AsyncClient(
            transport=models.MeteredTransport(
                db, config, inner=httpx.MockTransport(respond), thinking=kwargs["thinking"]
            )
        )

    monkeypatch.setattr(models, "http_client", client)

    class Probe(BaseModel):
        ok: bool

    with pytest.raises(models.AnswerModelUnavailable):
        await models.structured(service.config, service.db, Probe, "fixture", "fixture", retries=0)
    result = await models.structured(service.config, service.db, Probe, "fixture", "fixture", retries=0)
    assert result.ok
    assert len(responses) == 2



@pytest.mark.parametrize(
    "status,body",
    [
        (402, b'{"error": {"message": "Insufficient Balance"}}'),
        (429, b'{"error": {"message": "quota exhausted", "code": "insufficient_quota"}}'),
    ],
)
def test_an_account_out_of_credit_is_quota_exhausted(status, body):
    """DeepSeek answered 402 "Insufficient Balance" (2026-10-25); it was a generic http_error."""
    assert models.provider_rejection(httpx.Response(status), body)["rejection_category"] == "quota_exhausted"


def test_an_account_out_of_credit_holds_new_work_for_half_an_hour(service):
    ticket = provider_health.acquire(service.db, service.config, 25)
    started = time.time()
    provider_health.finish(service.db, service.config, ticket, failure="quota_exhausted")
    retry_at = service.db.get_setting(provider_health.key(service.config))["retry_at"]
    assert retry_at - started >= provider_health.QUOTA_COOLDOWN_SECONDS - 5
    assert provider_health.acquire(service.db, service.config, 25) is None
