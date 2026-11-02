"""Second security audit (2026-11-01, cso3): exact secrets are masked (nothing registered them before), a Telegram token inside its
own address is masked, deeply nested event data is masked, and files that hold a secret lose group and other access."""

import os
import stat
from types import SimpleNamespace

import pytest

from jobpilot import redaction
from jobpilot.redaction import MARK, redact, redact_data


@pytest.fixture(autouse=True)
def clean_registry():
    saved = (set(redaction._known), redaction._ordered)
    redaction._known.clear()
    redaction._ordered = ()
    yield
    redaction._known.clear()
    redaction._known.update(saved[0])
    redaction._ordered = saved[1]


def test_a_telegram_token_inside_its_bot_address_is_masked():
    token = "123456789:AA" + "Hk_-" * 8 + "xyz"
    for text in (f"https://api.telegram.org/bot{token}/sendMessage", f"Client error for url 'https://api.telegram.org/file/bot{token}/a.jpg'"):
        assert token not in redact(text)
        assert "Hk_-Hk_-" not in redact(text)


def test_settings_register_their_secrets_so_shapeless_values_are_masked(config):
    # prepare() ran in the fixture: the access token has no key shape, and used to reach events and messages unmasked.
    assert config.app_token not in redact(f"sent header Authorization-less {config.app_token} to the page")
    config.gmail_app_password = "abcdefghijklmnop"
    from jobpilot import connections

    connections.apply(config)
    assert "abcdefghijklmnop" not in redact("IMAP said no to abcdefghijklmnop for this mailbox")


def test_a_credential_that_passes_the_vault_is_masked_after_a_restart(config):
    from jobpilot.mail.vault import Vault

    vault = Vault(config)
    sealed = vault.seal({"email": "someone@example.invalid", "password": "Zq9!employer-pw", "session_storage": {"token": "should-not-register"}})
    assert "Zq9!employer-pw" not in redact("typed Zq9!employer-pw into the page")
    assert "someone@example.invalid" in redact("someone@example.invalid is not a secret")
    redaction._known.clear()
    redaction._ordered = ()
    assert "Zq9!employer-pw" in redact("typed Zq9!employer-pw")  # a fresh process knows nothing...
    vault.open(sealed)
    assert "Zq9!employer-pw" not in redact("typed Zq9!employer-pw")  # ...until the credential is read
    assert "should-not-register" in redact("a site's own session value should-not-register")


def test_event_data_nested_deeper_than_the_walk_is_still_masked():
    key = "sk-or-v1-" + "ab12" * 16
    deep = {"leaf": f"echo {key}"}
    for _ in range(15):
        deep = {"next": [deep]}
    assert key not in str(redact_data(deep))
    assert MARK in str(redact_data(deep))


def test_secret_files_and_the_backup_folder_lose_group_and_other_access(tmp_path, monkeypatch):
    from jobpilot.native import env

    data = tmp_path / "data"
    (data / "native").mkdir(parents=True)
    for name in env.SECRET_FILES:
        (data / name).write_text("x")
        (data / name).chmod(0o644)
    plain = data / "logos.txt"
    plain.write_text("x")
    plain.chmod(0o644)
    copies = tmp_path / "copies"
    copies.mkdir()
    copies.chmod(0o755)
    monkeypatch.setenv("AXIOM_BACKUP_DIR", str(copies))
    env.tighten(SimpleNamespace(data_dir=data))
    assert all(stat.S_IMODE((data / name).stat().st_mode) == 0o600 for name in env.SECRET_FILES)
    assert stat.S_IMODE(copies.stat().st_mode) == 0o700
    assert stat.S_IMODE(plain.stat().st_mode) == 0o644  # only the files that hold a secret


def test_the_test_clients_host_name_is_not_served_outside_a_test(client, monkeypatch):
    # "testserver" is a plain host name: a network that answers for it could aim a page at this computer. Only a running test
    # accepts it (pytest sets PYTEST_CURRENT_TEST; an installation never does).
    assert client.get("/api/control").status_code == 200
    monkeypatch.delenv("PYTEST_CURRENT_TEST")
    assert client.get("/api/control").status_code == 421
    assert client.get("/api/control", headers={"Host": "127.0.0.1:8080"}).status_code == 200
