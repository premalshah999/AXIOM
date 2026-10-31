"""HTTP contract of Settings > Database: the choice, the test, the move. Connection strings go in and never come back (2026-10-31)."""

import subprocess

from database_support import scratch_pg  # noqa: F401 - the fixture
from jobpilot.storage import choice

SUPABASE = "postgresql://postgres.abc:s3cret-pass@aws-0-us-east-1.pooler.supabase.com:5432/postgres"


def test_local_is_what_a_new_installation_reports_and_nothing_secret_is_in_it(client):
    answer = client.get("/api/database")
    assert answer.status_code == 200
    body = answer.json()
    assert body["mode"] == "local" and body["chosen"] == "local" and body["restart_needed"] is False and body["source"] == "default"
    assert body["options"]["supabase"] == {"configured": False, "target": ""} and body["running"] is False


def test_a_string_that_cannot_work_is_answered_with_the_reason_before_any_connection(client):
    answer = client.post("/api/database/test", json={"mode": "supabase", "url": SUPABASE.replace(":5432", ":6543")})
    assert answer.status_code == 422 and "transaction pooler" in answer.json()["detail"]
    assert client.post("/api/database/test", json={"mode": "elsewhere"}).status_code == 422


def test_a_connection_is_tested_without_being_saved(client, config, scratch_pg):
    name = scratch_pg.create("api")
    answer = client.post("/api/database/test", json={"mode": "server", "url": scratch_pg.url(name)})
    assert answer.status_code == 200 and answer.json()["ok"] is True
    assert "scratchpw" not in answer.text
    assert choice.sealed(config) == {}, "testing saves nothing"


def test_a_switch_starts_a_separate_process_with_no_string_on_its_command_line(client, config, monkeypatch, scratch_pg):
    name = scratch_pg.create("api")
    started = []
    monkeypatch.setattr(subprocess, "Popen", lambda args, **kw: started.append((args, kw)))
    url = scratch_pg.url(name)
    answer = client.post("/api/database/switch", json={"mode": "server", "url": url, "with_data": False})
    assert answer.status_code == 202
    args, kw = started[0]
    assert args[-4:] == ["database", "switch", "server", "--no-copy"] and "scratchpw" not in " ".join(args)
    assert kw["start_new_session"] is True
    assert "server_db_url" in choice.sealed(config), "the process reads it from the sealed file"
    assert client.post("/api/database/switch", json={"mode": "local"}).status_code == 409, "already on it"


def test_a_saved_string_can_be_forgotten_but_never_the_one_in_use(client, config):
    choice.keep_url(config, "server", "postgresql://u:p@db.example.org:5432/x")
    assert client.get("/api/database").json()["options"]["server"]["configured"] is True
    assert client.delete("/api/database/server").json()["options"]["server"]["configured"] is False
    assert client.delete("/api/database/local").status_code == 404
