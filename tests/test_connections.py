"""Service keys are entered in the dashboard (Setup > Connections) instead of .env (owner 2026-10-26)."""

import pytest

from jobpilot import connections
from jobpilot.config import Settings


def config(tmp_path, **env):
    return Settings(data_dir=tmp_path, deepseek_api_key=env.get("deepseek", ""), telegram_user_id="",
                    twocaptcha_api_key="", haiku_api_key="").prepare()


def test_a_saved_key_wins_over_env_and_clearing_falls_back(tmp_path):
    c = config(tmp_path, deepseek="env-key-1234")
    assert c.deepseek_api_key == "env-key-1234"
    connections.save(c, {"deepseek_api_key": "app-key-56789", "llm_provider": "deepseek"})
    assert c.deepseek_api_key == "app-key-56789"
    again = Settings(data_dir=tmp_path, deepseek_api_key="env-key-1234").prepare()  # a restart reads the file
    assert again.deepseek_api_key == "app-key-56789"
    connections.save(c, {"deepseek_api_key": ""})
    assert c.deepseek_api_key == "env-key-1234"


def test_secrets_are_encrypted_at_rest_and_masked_in_status(tmp_path):
    c = config(tmp_path)
    connections.save(c, {"haiku_api_key": "sk-ant-secret-abcdef", "telegram_user_id": "12345"})
    assert b"sk-ant-secret" not in (tmp_path / connections.FILE).read_bytes()
    fields = {f["name"]: f for f in connections.status(c)["fields"]}
    assert fields["haiku_api_key"]["value"] == "••••cdef" and fields["haiku_api_key"]["source"] == "app"
    assert fields["telegram_user_id"]["value"] == "12345"


@pytest.mark.parametrize("update,message", [
    ({"telegram_user_id": "@someone"}, "number"),
    ({"llm_provider": "gpt"}, "deepseek"),
    ({"not_a_field": "x"}, "Unknown"),
])
def test_invalid_values_are_refused(tmp_path, update, message):
    with pytest.raises(ValueError, match=message):
        connections.save(config(tmp_path), update)


async def test_an_unset_key_tests_as_missing(tmp_path):
    result = await connections.test(config(tmp_path), "twocaptcha_api_key")
    assert result == {"ok": False, "message": "Nothing is saved for this yet."}
