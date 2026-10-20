import httpx
import pytest

from jobpilot import telegram


async def test_failed_telegram_call_never_exposes_the_bot_token(monkeypatch):
    class Config:
        telegram_enabled = True
        telegram_bot_token = "123456:SECRET-TOKEN"
        telegram_worker_slot = None

    transport = httpx.MockTransport(
        lambda request: httpx.Response(400, json={"ok": False, "description": "Bad Request: x"})
    )
    real = httpx.AsyncClient
    monkeypatch.setattr(telegram.httpx, "AsyncClient", lambda **kw: real(transport=transport, **kw))
    with pytest.raises(RuntimeError) as exc:
        await telegram.api(Config(), "sendMessage", {"chat_id": 1})
    assert "SECRET-TOKEN" not in str(exc.value) and "HTTP 400" in str(exc.value)
