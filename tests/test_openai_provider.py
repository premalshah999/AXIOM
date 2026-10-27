"""OpenAI GPT-6 Luna as a provider (owner 2026-10-27: "set it at cheapest configuration almost always"): reasoning
effort "none" (a stronger retry tier gets "low"), no thinking switch, no sampling overrides, max_completion_tokens."""

import json

import httpx
import pytest
from pydantic import BaseModel

from jobpilot import models
from jobpilot.connections import PROVIDERS, save as save_connections


def completion(content='{"ok":true}'):
    return {
        "id": "fixture", "object": "chat.completion", "created": 0, "model": "gpt-6-luna",
        "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }


@pytest.mark.parametrize("tier,effort", [(1, "none"), (2, "low")])
async def test_luna_requests_use_the_cheapest_setting(service, monkeypatch, tier, effort):
    config = service.config.model_copy(update={"llm_provider": "openai", "openai_api_key": "fixture-only"})
    captured = []

    async def respond(request):
        captured.append((request.url.host, request.url.path, json.loads(request.content)))
        return httpx.Response(200, json=completion())

    def client(db, cfg, run_id=None, **kwargs):
        return httpx.AsyncClient(transport=models.MeteredTransport(
            db, cfg, inner=httpx.MockTransport(respond), thinking=kwargs["thinking"], tier=tier))

    monkeypatch.setattr(models, "http_client", client)

    class Probe(BaseModel):
        ok: bool

    assert (await models.structured(config, service.db, Probe, "fixture", "fixture", retries=0)).ok
    host, path, body = captured[0]
    assert (host, path, body["model"]) == ("api.openai.com", "/v1/chat/completions", "gpt-6-luna")
    assert body["reasoning_effort"] == effort and "thinking" not in body
    assert "temperature" not in body and "max_completion_tokens" in body and "max_tokens" not in body


def test_luna_is_a_connection_provider(service):
    assert "openai" in PROVIDERS
    saved = save_connections(service.config, {"llm_provider": "openai"})
    assert saved.llm_provider == "openai" and saved.llm_model == "gpt-6-luna"
