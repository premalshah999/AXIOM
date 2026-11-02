"""Settings › every saved value: refused in plain words when out of range, kept after a restart, and read by the engine."""

from pathlib import Path

import pytest

from jobpilot import connections
from jobpilot.config import Settings


def config(tmp_path, **env):
    return Settings(_env_file=None, data_dir=tmp_path, **env).prepare()


@pytest.mark.parametrize("update,words", [
    ({"daily_application_limit": "1001"}, "Applications per day must be 1000 or less"),
    ({"daily_application_limit": "0"}, "Applications per day must be 1 or more"),
    ({"daily_application_limit": "ten"}, "Applications per day isn't a valid value"),
    ({"daily_budget_usd": "0"}, "must be more than 0$"),
    ({"max_model_calls": "301"}, "AI calls per application must be 300 or less"),
    ({"captcha_timeout": "5"}, "Seconds to wait for a solve must be 10 or more"),
    ({"captcha_image_rounds": "9"}, "Paid image rounds per puzzle must be 8 or less"),
    ({"normal_workers": "5"}, "choose 4 or 6 or 8"),
    ({"timezone": "New York"}, "Use a name like America/New_York"),
    ({"timezone": "America"}, "Use a name like America/New_York"),
    ({"timezone": "../etc/passwd"}, "Use a name like America/New_York"),
    ({"account_email": "not an email"}, "name@example.com"),
    ({"vision_base_url": "openrouter.ai"}, "https://"),
    ({"application_password": "short"}, "Main password: use at least 10 characters"),
])
def test_a_value_out_of_range_says_so_in_plain_words(tmp_path, update, words):
    with pytest.raises(ValueError, match=words) as refused:
        connections.save(config(tmp_path), update)
    assert "pydantic" not in str(refused.value) and "errors.pydantic.dev" not in str(refused.value)


def test_the_daily_limit_goes_to_1000(tmp_path):
    assert connections.save(config(tmp_path), {"daily_application_limit": "1000"}).daily_application_limit == 1000


def test_every_saved_value_comes_back_after_a_restart(tmp_path):
    values = {
        "daily_application_limit": "777", "daily_budget_usd": "7.5", "max_model_calls": "42", "timezone": "Europe/London",
        "captcha_timeout": "120", "captcha_image_rounds": "2", "telegram_user_id": "12345", "telegram_enabled": "false",
        "company_logos": "false", "openrouter_provider": "wafer", "openrouter_fallback_hosts": "false",
        "account_email": "alex@example.test", "normal_workers": "6", "specialist_workers": "2", "custom_ats_worker": "false",
        "vision_base_url": "https://vision.example.test/v1", "vision_model": "synthetic-vision",
    }
    connections.save(config(tmp_path), values)
    again = config(tmp_path)  # A restart reads the file.
    assert again.daily_application_limit == 777 and again.daily_budget_usd == 7.5 and again.max_model_calls == 42
    assert again.timezone == "Europe/London" and again.captcha_timeout == 120 and again.captcha_image_rounds == 2
    assert again.telegram_user_id == "12345" and again.telegram_enabled is False and again.company_logos is False
    assert again.openrouter_provider == "wafer" and again.openrouter_fallback_hosts is False
    assert again.account_email == "alex@example.test" and again.normal_workers == 6 and again.specialist_workers == 2
    assert again.custom_ats_worker is False
    assert again.vision_base_url == "https://vision.example.test/v1" and again.vision_model == "synthetic-vision"


def test_a_bad_time_zone_already_saved_cannot_stop_the_app(tmp_path):
    """A build that did not check saved whatever was typed, and every "today" reads it: the dashboard's poll answered 500."""
    from zoneinfo import ZoneInfo

    c = config(tmp_path)
    data = connections._cipher(c).encrypt(b'{"timezone": "New York"}')
    (tmp_path / connections.FILE).write_bytes(data)
    assert ZoneInfo(config(tmp_path).timezone)
    assert ZoneInfo(config(tmp_path, timezone="Not/AZone").timezone)


def test_the_browser_switch_is_not_offered_where_it_cannot_apply(tmp_path):
    """The Mac app attaches its own visible worker Chrome. HEADLESS only reaches the throwaway one used without it."""
    names = lambda c: {f["name"] for f in connections.status(c)["fields"]}
    assert "headless" not in names(config(tmp_path, browser_cdp_url="http://127.0.0.1:9224"))
    assert "headless" in names(config(tmp_path, browser_cdp_url=""))


class _Reply:
    def __init__(self, status, body=None):
        self.status_code, self._body = status, body if body is not None else {}

    def json(self):
        return self._body


def _service(monkeypatch, replies):
    """Answers by the last part of the address. A value that is an exception is raised."""
    calls = []

    class Client:
        def __init__(self, *a, **k): ...
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False

        def _answer(self, url, **kwargs):
            calls.append((url, kwargs))
            answer = replies[url.rsplit("/", 1)[-1]]
            if isinstance(answer, Exception):
                raise answer
            return answer

        async def get(self, url, **kwargs): return self._answer(url, **kwargs)
        async def post(self, url, **kwargs): return self._answer(url, **kwargs)

    monkeypatch.setattr(connections.httpx, "AsyncClient", Client)
    return calls


async def test_telegram_tests_the_token_and_that_the_bot_can_reach_you(tmp_path, monkeypatch):
    c = config(tmp_path, telegram_bot_token="123:synthetic", telegram_user_id="")
    _service(monkeypatch, {"getMe": _Reply(404)})
    refused = await connections.test(c, "telegram_bot_token")
    assert refused["ok"] is False and "@BotFather" in refused["message"]  # Telegram's answer to a mistyped token is 404.
    _service(monkeypatch, {"getMe": _Reply(200)})
    nobody = await connections.test(c, "telegram_bot_token")
    assert nobody["ok"] is False and "user ID" in nobody["message"]
    c.telegram_user_id = "12345"
    _service(monkeypatch, {"getMe": _Reply(200), "sendChatAction": _Reply(400)})
    unstarted = await connections.test(c, "telegram_bot_token")
    assert unstarted["ok"] is False and "press Start" in unstarted["message"]
    calls = _service(monkeypatch, {"getMe": _Reply(200), "sendChatAction": _Reply(200)})
    assert (await connections.test(c, "telegram_bot_token"))["ok"] is True
    assert calls[-1][1]["json"] == {"chat_id": "12345", "action": "typing"}  # Nothing lasting appears in the chat.


async def test_a_typesafe_key_is_really_asked_about(tmp_path, monkeypatch):
    """Test answered Works for any text at all."""
    c = config(tmp_path, typesafe_api_key="ts-synthetic")
    _service(monkeypatch, {"systemone": _Reply(401)})
    refused = await connections.test(c, "typesafe_api_key")
    assert refused["ok"] is False and "TypeSafe" in refused["message"]
    _service(monkeypatch, {"systemone": _Reply(200, {"answers": {}})})
    assert (await connections.test(c, "typesafe_api_key"))["ok"] is True


async def test_a_page_where_json_belonged_is_a_plain_failure(tmp_path, monkeypatch):
    class NotJson(_Reply):
        def json(self):
            raise ValueError("not JSON")

    c = config(tmp_path, twocaptcha_api_key="synthetic")
    _service(monkeypatch, {"res.php": NotJson(200)})
    result = await connections.test(c, "twocaptcha_api_key")
    assert result["ok"] is False and "2Captcha" in result["message"]


def test_use_this_one_moves_what_the_engine_runs(client, service):
    """A save under Advanced writes both roles. Afterwards "Use this one" changed a setting nothing read, while the card said In use."""
    from jobpilot.agent_settings import for_role

    config_ = service.config
    assert client.put("/api/connections", json={"values": {"deepseek_api_key": "ds-synthetic", "openrouter_api_key": "or-synthetic"}}).status_code == 200
    agents = client.get("/api/agents").json()
    agents["enabled"]["captcha"] = False
    answer = client.put("/api/agents", json={"enabled": agents["enabled"], "models": agents["models"]})
    assert answer.status_code == 200
    moved = client.put("/api/connections", json={"values": {"llm_provider": "openrouter"}}).json()
    assert moved["provider"] == "openrouter" and moved["model_ready"] is True
    assert for_role(service.db, config_, "answers").llm_provider == "openrouter"
    assert for_role(service.db, config_, "navigator").llm_provider == "openrouter"
    assert client.get("/api/agents").json()["enabled"]["captcha"] is False  # The helpers stay as they were.


def test_a_model_chosen_under_advanced_is_the_one_connections_shows(client, service):
    """Ranking and the checks before a run read the main model's key: another provider chosen here left them with none."""
    assert client.put("/api/connections", json={"values": {"llm_provider": "deepseek", "openrouter_api_key": "or-synthetic", "deepseek_api_key": ""}}).status_code == 200
    service.config.deepseek_api_key = ""
    agents = client.get("/api/agents").json()
    answer = client.put("/api/agents", json={"enabled": agents["enabled"], "models": {"answers": "openrouter", "navigator": "openrouter"}})
    assert answer.status_code == 200
    assert service.config.llm_provider == "openrouter" and service.config.llm_api_key
    shown = client.get("/api/connections").json()
    assert shown["provider"] == "openrouter" and shown["model_ready"] is True


def test_a_time_zone_that_is_not_one_is_refused_over_http(client):
    answer = client.put("/api/connections", json={"values": {"timezone": "New York"}})
    assert answer.status_code == 400 and "America/New_York" in answer.json()["detail"]
    assert client.get("/api/snapshot?include_sources=false").status_code == 200


FRONTEND = Path(__file__).resolve().parent.parent / "frontend" / "src"


def test_the_overview_limit_reaches_what_the_server_allows():
    """The field said 1 to 1000 and quietly saved 500."""
    from jobpilot.config import Settings as S

    most = next(m.le for m in S.model_fields["daily_application_limit"].metadata if hasattr(m, "le"))
    source = (FRONTEND / "batch.tsx").read_text()
    assert f"MAX_DAILY_LIMIT = {most}" in source and "Math.min(500" not in source


def test_profile_rules_keep_what_the_page_has_no_field_for():
    """history_day (the setup guide), travel_choice and employment_countries went back to their defaults on any rule change."""
    source = (FRONTEND / "profile-rules.tsx").read_text()
    body = source[source.index("export function rulesWithDefaults"):]
    assert "...saved," in body[: body.index("education: saved.education")]


def test_a_saved_rule_the_page_cannot_show_survives_a_profile_save(client):
    """The server's side of the same promise: what is sent back is what is kept."""
    profile = client.get("/api/profile").json()
    rules = {"history_day": "first", "travel_choice": "maximum", "employment_countries": ["United States"]}
    profile["answer_rules"] = {**(profile.get("answer_rules") or {}), **rules}
    assert client.put("/api/profile", json=profile).status_code == 200
    saved = client.get("/api/profile").json()["answer_rules"]
    assert {k: saved[k] for k in rules} == rules
