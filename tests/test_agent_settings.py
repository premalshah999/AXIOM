import json

import httpx
import pytest
from pydantic import BaseModel
from types import SimpleNamespace
from unittest.mock import AsyncMock

from jobpilot import models
from jobpilot.agent_settings import AgentDisabled, current, for_role, save, summary
from jobpilot.db import Budget


def test_rate_limit_backoff_honors_retry_after_and_keeps_one_productive_recovery(service, monkeypatch):
    from types import SimpleNamespace
    import httpx
    from jobpilot import provider_health
    from jobpilot.models import provider_rejection

    clock = [1000.0]
    monkeypatch.setattr(provider_health, "time", SimpleNamespace(time=lambda: clock[0]))
    response = httpx.Response(429, headers={"retry-after": "12"})
    diagnostic = provider_rejection(response, b'{"error":{"message":"rate limit; private-fixture-value"}}')
    assert diagnostic == {"rejection_category": "rate_limited", "retry_after_seconds": 12}
    ticket = provider_health.acquire(service.db, service.config, 25)
    provider_health.finish(service.db, service.config, ticket, failure="rate_limited", retry_after=12)
    assert provider_health.acquire(service.db, service.config, 25) is None
    stored = service.db.get_setting(provider_health.key(service.config))
    assert stored["retry_at"] == 1012 and "private-fixture-value" not in str(stored)
    clock[0] = 1013
    assert provider_health.dispatch_limit(service.db, service.config) == service.config.workers
    recovery = provider_health.acquire(service.db, service.config, 25)
    assert recovery is not None
    assert provider_health.acquire(service.db, service.config, 25) is None
    provider_health.finish(service.db, service.config, recovery)
    assert provider_health.status(service.db, service.config)["state"] == "ready"


def test_quota_rejection_is_distinct_from_transient_rate_limit():
    import httpx
    from jobpilot.models import provider_rejection

    result = provider_rejection(httpx.Response(429), b'{"error":{"code":"insufficient_quota"}}')
    assert result == {"rejection_category": "quota_exhausted", "retry_after_seconds": None}


def test_settings_roundtrip_and_secret_free(client, service):
    service.config.haiku_api_key = "private-fixture-key"
    initial = client.get("/api/agents")
    assert initial.status_code == 200
    assert set(initial.json()["enabled"]) == {
        "answers",
        "accounts",
        "email",
        "captcha",
        "navigator",
        "discovery",
    }
    value = initial.json()
    value.pop("providers")
    value["models"]["answers"] = "haiku"
    value["enabled"]["captcha"] = False
    response = client.put("/api/agents", json=value)
    assert response.status_code == 200
    assert response.json()["models"]["answers"] == "haiku"
    assert not response.json()["enabled"]["captcha"]
    assert "private-fixture-key" not in response.text
    assert for_role(service.db, service.config, "answers").llm_provider == "haiku"
    assert service.config.llm_provider == "mimo"


def test_unconfigured_and_unrecognized_provider_rejected(client, service):
    value = current(service.db, service.config).model_dump()
    value["models"]["answers"] = "haiku"
    assert client.put("/api/agents", json=value).status_code == 422
    value["models"]["answers"] = "arbitrary"
    assert client.put("/api/agents", json=value).status_code == 422
    assert client.get("/api/agents").json()["models"]["answers"] == "mimo"


async def test_disabled_helpers_do_not_call_provider_mail_or_discovery(service, monkeypatch):
    value = current(service.db, service.config).model_dump()
    value["enabled"] = dict.fromkeys(value["enabled"], False)
    save(service.db, service.config, value)

    class Probe(BaseModel):
        ok: bool

    with pytest.raises(AgentDisabled):
        await models.structured(service.config, service.db, Probe, "fixture", "fixture")
    # No mailbox connection or polling side effect while disabled.
    service.mail.poll_lock = None
    assert await service.mail.poll_once() is None
    from jobpilot.discovery import discover

    with pytest.raises(AgentDisabled):
        await discover({}, service.db, service.config)
    from jobpilot.captcha import CaptchaSolver

    solver = CaptchaSolver(service.config, lambda *args: None, service.db)
    frame = SimpleNamespace(
        url="https://owned.test",
        evaluate=AsyncMock(
            return_value={"kind": "hcaptcha", "key": "owned", "generation": 1, "id": "widget"}
        ),
    )
    assert not await solver.solve(SimpleNamespace(frames=[frame]))
    assert "disabled" in solver.last_reason
    other = CaptchaSolver(service.config, lambda *args: None, service.db)
    assert not await other.solve(SimpleNamespace(frames=[]))
    assert not other.challenge_unverified
    # Adapters must not open or act on a page before the model-navigation gate.
    from jobpilot.browser import BrowserEngine

    launch = AsyncMock()
    monkeypatch.setattr("jobpilot.browser.async_playwright", launch)
    with pytest.raises(AgentDisabled):
        await BrowserEngine(service, "disabled-navigation").run({}, {})
    launch.assert_not_called()
    from jobpilot.workday import WorkdayAuth

    engine = SimpleNamespace(db=service.db, page=None, emit=lambda *args: None)
    with pytest.raises(AgentDisabled):
        await WorkdayAuth(engine).recover()


async def test_native_haiku_request_schema_and_metering(service, monkeypatch):
    service.config.haiku_api_key = "fixture-only"
    value = current(service.db, service.config).model_dump()
    value["models"]["answers"] = "haiku"
    save(service.db, service.config, value)
    captured = []

    async def respond(request):
        captured.append(json.loads(request.content))
        assert request.url.path == "/v1/messages"
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

    result = await models.structured(service.config, service.db, Probe, "fixture", "fixture", retries=0)
    assert result.ok
    assert len(captured) == 1
    assert captured[0]["thinking"] == {"type": "disabled"}
    assert "temperature" not in captured[0] and "max_completion_tokens" not in captured[0]
    with service.db.session() as session:
        assert session.query(Budget).one().spent == pytest.approx((10 * 0.1 + 5 * 0.5) / 1_000_000)
    assert summary(service.db, service.config)["providers"]["haiku"]["available"]


def test_haiku_55_context_price_boundary(service):
    config = service.config.model_copy(update={"llm_provider": "haiku"})
    assert models.token_cost(config, 100_000, 1000) == pytest.approx(0.0105)
    assert models.token_cost(config, 100_001, 1000) == pytest.approx(0.0525005)
