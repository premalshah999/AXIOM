"""Launch-review hardening (2026-11-01): the local API refuses pages that are not the dashboard, secrets are masked wherever text is
stored or sent, the data key lives in the Keychain in the installed app, and a hostile release answer cannot send the person elsewhere."""

import os
import stat
import sys

import pytest

from jobpilot.redaction import MARK, redact


# --- the local API ---------------------------------------------------------------------------------------------------


def test_a_foreign_host_name_is_refused_so_a_rebound_domain_reaches_nothing(client, config):
    # DNS rebinding: evil.example re-pointed at 127.0.0.1 sends its own name as Host.
    assert client.get("/api/control", headers={"Host": "evil.example:8080"}).status_code == 421
    assert client.get("/", headers={"Host": "rebind.evil.example"}).status_code == 421
    for ok in ("127.0.0.1:8080", "localhost:8080", "[::1]:8080"):
        assert client.get("/api/control", headers={"Host": ok}).status_code == 200


def test_the_configured_base_url_host_is_served(client, config, monkeypatch):
    config.base_url = "http://192.168.1.20:8080"
    # The middleware read BASE_URL when the app was built; a new app for a LAN address serves that name.
    from fastapi.testclient import TestClient
    from jobpilot.api import create_app

    monkeypatch.setattr("jobpilot.api.app.Database", lambda url: client.app.state.service.db)
    monkeypatch.setattr("jobpilot.api.app.Service", lambda db, cfg: client.app.state.service)
    lan = TestClient(create_app(config), headers={"Authorization": "Bearer " + config.app_token})
    assert lan.get("/api/control", headers={"Host": "192.168.1.20:8080"}).status_code == 200
    assert lan.get("/api/control", headers={"Host": "evil.example"}).status_code == 421


def test_a_write_from_another_site_is_refused_without_an_origin_header(client):
    for site in ("cross-site", "same-site"):
        answer = client.post("/api/mail-challenges/unknown/cancel", json={}, headers={"Sec-Fetch-Site": site})
        assert answer.status_code == 403
    assert client.post("/api/mail-challenges/unknown/cancel", json={}, headers={"Sec-Fetch-Site": "same-origin"}).status_code == 400


# --- masking -----------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "secret",
    [
        "sk-or-v1-" + "ab12" * 16,
        "sk-ant-api03-" + "Zy_-9" * 8,
        "sk-proj-" + "q" * 30,
        "123456789:AA" + "Hk_-" * 8 + "xyz",
        "AIza" + "Sy" * 18,
        "ghp_" + "a1" * 20,
        "xoxb-1234567890-abcdef",
        "AKIA" + "ABCDEFGHIJKLMNOP",
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dBjftJeZ4CVPmB92K27uhbUJU1p1r",
        "A" * 43 + "=",
    ],
)
def test_key_shaped_strings_are_masked_in_any_text(secret):
    text = f"upstream said: rejected {secret} for this request"
    assert secret not in redact(text)
    assert MARK in redact(text)


def test_registered_values_are_masked_whatever_their_format():
    from jobpilot import redaction

    redaction.register(["Corp-Pass 9876!x"])
    try:
        assert "9876" not in redact("typed Corp-Pass 9876!x and Corp-Pass9876!x")
    finally:
        redaction._known.clear()
        redaction._ordered = ()


def test_loose_masking_catches_labelled_credentials_and_leaves_plain_prose():
    assert "hunter22" not in redact("password: hunter22 sent", loose=True)
    assert "abcd1234" not in redact("GET /x?api_key=abcd1234&b=2", loose=True)
    assert "pw99" not in redact("https://user:pw99@host.example/", loose=True)
    assert redact("Choose a password with 10 characters", loose=True) == "Choose a password with 10 characters"


def test_an_event_never_stores_a_key_echoed_by_a_service(service):
    key = "sk-or-v1-" + "cd34" * 16
    service.db.event("run-1", "model_error", f"HTTP 401 for key {key}", {"detail": {"body": f"bad {key}"}})
    service.db.flush_events()
    from sqlalchemy import select
    from jobpilot.db import Event

    with service.db.session() as s:
        rows = list(s.scalars(select(Event).where(Event.run_id == "run-1")))
    assert rows and all(key not in row.message and key not in str(row.data) for row in rows)


def test_a_diagnostics_export_masks_a_key_on_a_line_with_no_label():
    from jobpilot.native.desktop import scrub

    key = "sk-or-v1-" + "ef56" * 16
    out = scrub(f"2026-11-01 10:00:00 model call failed {key}\nBearer {'t' * 30}\nbot 123456789:AA{'Hk_-' * 8}xyz")
    assert key not in out and "ttttttttt" not in out and "Hk_-Hk_-" not in out


def test_update_check_offers_only_this_projects_release_pages(tmp_path):
    from jobpilot.native import desktop

    class Layout:
        bundle = tmp_path

    for url in ("https://github.com/someone-else/repo/releases/1", "https://github.com.evil.example/premalshah999/AXIOM/", "javascript:alert(1)"):
        answer = desktop.check_update(Layout, fetch=lambda url=url: {"tag_name": "v99", "html_url": url})
        assert answer["url"] == desktop.RELEASE_PAGE


# --- the data key -----------------------------------------------------------------------------------------------------


STUB = """#!{python}
import os, sys
store = os.environ["STUB_KEYCHAIN"]
if os.environ.get("STUB_MODE") == "locked":
    sys.exit(36)
args = sys.argv[1:]
if args[:1] == ["-i"]:
    import shlex
    words = shlex.split(sys.stdin.read())
    account = words[words.index("-a") + 1]
    open(store + "." + account, "w").write(words[words.index("-w") + 1])
    sys.exit(0)
account = args[args.index("-a") + 1]
path = store + "." + account
if not os.path.exists(path):
    sys.exit(44)
print(open(path).read())
"""


class Cfg:
    def __init__(self, data_dir, key=""):
        self.data_dir, self.mail_encryption_key = data_dir, key
