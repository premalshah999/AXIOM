from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from pydantic import BaseModel, Field, ValidationError

from jobpilot import models


class Decision(BaseModel):
    action: str
    query: str = Field(default="", max_length=10)


def fake_anthropic(replies, seen):
    class Messages:
        async def create(self, **kwargs):
            seen.append(kwargs["messages"])
            text = replies.pop(0)
            return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)])

    class Client:
        def __init__(self, **kwargs):
            self.messages = Messages()

    return Client


@pytest.fixture
def haiku(monkeypatch, config, service):
    @asynccontextmanager
    async def client(*args, **kwargs):
        yield None

    monkeypatch.setattr(models, "http_client", client)
    monkeypatch.setattr("jobpilot.agent_settings.require", lambda db, helper: None)
    monkeypatch.setattr(
        "jobpilot.agent_settings.for_role",
        lambda db, cfg, role: cfg.model_copy(update={"llm_provider": "haiku", "haiku_api_key": "k"}),
    )
    return service.db


async def test_haiku_reply_gets_one_schema_repair(haiku, config, monkeypatch):
    seen = []
    replies = [
        'Here you go: {"action": "search", "query": "far too long a query"}',
        '{"action": "search", "query": "short"}',
    ]
    monkeypatch.setattr("anthropic.AsyncAnthropic", fake_anthropic(replies, seen))
    result = await models.structured(config, haiku, Decision, "instructions", "prompt", run_id="r1")
    assert result.query == "short" and len(seen) == 2
    assert "did not match the schema" in seen[1][-1]["content"]


async def test_haiku_repair_is_bounded(haiku, config, monkeypatch):
    seen = []
    replies = ['{"query": 1}', '{"query": 2}']
    monkeypatch.setattr("anthropic.AsyncAnthropic", fake_anthropic(replies, seen))
    with pytest.raises(ValidationError):
        await models.structured(config, haiku, Decision, "instructions", "prompt", run_id="r1")
    assert len(seen) == 2
