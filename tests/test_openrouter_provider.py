"""OpenRouter as a provider (owner 2026-10-27): DeepSeek V4.1 Flash 1017 served only by Relace, reasoning off, and
cached input read from OpenRouter's prompt_tokens_details so the cheap cache rate is what the budget records."""

import json

import httpx
from pydantic import BaseModel

from jobpilot import models
from jobpilot.agent_settings import missing_key_message
from jobpilot.connections import PROVIDERS, save as save_connections


def completion(content='{"ok":true}'):
    return {
        "id": "fixture", "object": "chat.completion", "created": 0, "model": "deepseek/deepseek-v4.1-flash-1017",
        "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": 1000, "completion_tokens": 10, "total_tokens": 1010,
                  "prompt_tokens_details": {"cached_tokens": 800}, "cost": 0.00003},
    }


async def test_requests_go_to_relace_with_reasoning_off(service, monkeypatch):
    config = service.config.model_copy(update={"llm_provider": "openrouter", "openrouter_api_key": "fixture-only"})
    captured = []

    async def respond(request):
        captured.append((request.url.host, request.url.path, json.loads(request.content)))
        return httpx.Response(200, json=completion())

    def client(db, cfg, run_id=None, **kwargs):
        return httpx.AsyncClient(transport=models.MeteredTransport(
            db, cfg, inner=httpx.MockTransport(respond), thinking=kwargs["thinking"]))

    monkeypatch.setattr(models, "http_client", client)

    class Probe(BaseModel):
        ok: bool

    assert (await models.structured(config, service.db, Probe, "fixture", "fixture", retries=0)).ok
    host, path, body = captured[0]
    assert (host, path, body["model"]) == ("openrouter.ai", "/api/v1/chat/completions", "deepseek/deepseek-v4.1-flash-1017")
    assert body["provider"] == {"order": ["relace"], "allow_fallbacks": False}
    assert body["reasoning"] == {"enabled": False} and "thinking" not in body and "reasoning_effort" not in body
    assert "max_tokens" in body and "max_completion_tokens" not in body


def test_cached_input_is_billed_at_the_cache_rate(service):
    config = service.config.model_copy(update={"llm_provider": "openrouter"})
    usage = completion()["usage"]
    assert models.cache_hits(usage) == 800
    cost = models.token_cost(config, 1000, 10, models.cache_hits(usage))
    assert abs(cost - (200 * 0.07 + 800 * 0.008 + 10 * 0.50) / 1_000_000) < 1e-12
    assert models.cache_hits({"prompt_cache_hit_tokens": 5}) == 5 and models.cache_hits({}) == 0


def test_openrouter_is_a_connection_provider(service):
    assert "openrouter" in PROVIDERS
    saved = save_connections(service.config, {"llm_provider": "openrouter"})
    assert saved.llm_provider == "openrouter" and saved.llm_model == "deepseek/deepseek-v4.1-flash-1017"
    assert "OPENROUTER_API_KEY" in missing_key_message("openrouter")
