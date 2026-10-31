"""The database choice: Local unless someone deliberately picks another, and a pick that cannot work refuses to start (2026-10-31)."""

import json
import stat

import pytest

from jobpilot.config import Settings
from jobpilot.db import DURABLE_EVENTS, Database, engine_options, remote_options
from jobpilot.storage import choice, mode
from jobpilot.storage.choice import DatabaseChoiceError

SUPABASE = "postgresql://postgres.abc:s3cret-pass@aws-0-us-east-1.pooler.supabase.com:5432/postgres"
SERVER = "postgresql://axiom:s3cret-pass@db.example.org:5432/axiom"


def settings(tmp_path, **extra):
    return Settings(**{"_env_file": None, "data_dir": tmp_path, "app_token": "t", **extra})


def test_local_is_the_default_and_a_person_who_never_opens_the_screen_keeps_it(tmp_path):
    config = settings(tmp_path).prepare()
    assert config.database_mode == "local" and config.database_url.startswith("sqlite")
    assert not (tmp_path / mode.FILE).exists(), "choosing nothing writes nothing"
    compose = "postgresql+psycopg://applypilot:p@db:5432/applypilot"
    assert settings(tmp_path, database_url=compose).prepare().database_url == compose
    assert engine_options(compose) == {"pool_pre_ping": True, "pool_size": 10, "max_overflow": 20, "pool_timeout": 30}


def test_saved_connection_strings_do_nothing_until_a_remote_database_is_chosen(tmp_path):
    choice.keep_url(settings(tmp_path).prepare(), "supabase", SUPABASE)
    config = settings(tmp_path).prepare()
    assert config.database_mode == "local" and config.database_url.startswith("sqlite")


def test_a_remote_choice_replaces_both_urls_and_keeps_the_local_ones(tmp_path):
    local = "postgresql+psycopg://applypilot:p@127.0.0.1:54329/applypilot"
    config = settings(tmp_path, database_mode="supabase", supabase_db_url=SUPABASE, database_url=local, dbos_database_url=local).prepare()
    assert "supabase.com" in config.database_url and "sslmode=require" in config.database_url
    assert "axiom_schema=axiom" in config.database_url and "axiom_" not in config.dbos_database_url
    assert choice.local_urls(config) == (local, local)
    clean, options = remote_options(config.database_url)
    assert "axiom_" not in clean and options["axiom_schema"] == "axiom"
    assert (options["axiom_pool"], options["axiom_overflow"]) == ("6", "4")


@pytest.mark.parametrize("scheme", ["postgres://", "postgresql://", "postgresql+psycopg://"])
def test_any_spelling_of_the_string_is_normalised(scheme):
    url = choice.normalise(SUPABASE.replace("postgresql://", scheme), "supabase")
    assert url.startswith("postgresql+psycopg://") and "sslmode=require" in url and "keepalives=1" in url
    assert "sslmode=verify-full" in choice.normalise(SUPABASE + "?sslmode=verify-full", "supabase")
    assert "sslmode=require" in choice.normalise(SUPABASE + "?sslmode=disable", "supabase"), "Supabase is never unencrypted"
    assert "pgbouncer" not in choice.normalise(SUPABASE + "?pgbouncer=true", "supabase")


@pytest.mark.parametrize("raw", [SUPABASE.replace(":5432", ":6543"), SERVER.replace(":5432", ":6543")])
def test_the_transaction_pooler_is_refused_with_the_reason_and_the_way_out(raw):
    with pytest.raises(DatabaseChoiceError, match=r"6543.*prepared statements.*Session pooler.*5432"):
        choice.normalise(raw, "supabase")


@pytest.mark.parametrize(
    "bad, message",
    [
        ("", "Paste the Supabase connection string"),
        ("postgresql://postgres:[YOUR-PASSWORD]@db.abc.supabase.co:5432/postgres", "YOUR-PASSWORD"),
        ("mysql://u:p@host/db", "must start with postgresql://"),
        ("postgresql://u:p@/db", "host name"),
        ("postgresql://u@host/db", "password"),
    ],
)
def test_a_string_that_cannot_work_is_explained(bad, message):
    with pytest.raises(DatabaseChoiceError, match=message):
        choice.normalise(bad, "supabase")


def test_a_server_on_the_internet_is_encrypted_unless_the_string_says_otherwise():
    assert "sslmode=require" in choice.normalise(SERVER, "server")
    assert "sslmode" not in choice.normalise("postgresql://u:p@192.168.1.20/db", "server")
    assert "sslmode=disable" in choice.normalise(SERVER + "?sslmode=disable", "server")
    assert choice.is_private_host("localhost") and choice.is_private_host("10.0.0.5") and choice.is_private_host("nas")
    assert not choice.is_private_host("db.example.org") and not choice.is_private_host("8.8.8.8")


def test_a_remote_choice_that_cannot_be_used_refuses_to_start_rather_than_write_locally(tmp_path):
    with pytest.raises(DatabaseChoiceError, match=r"set to Supabase.*Paste the Supabase connection string.*DATABASE_MODE=local"):
        settings(tmp_path, database_mode="supabase").prepare()
    with pytest.raises(DatabaseChoiceError, match="6543"):
        settings(tmp_path, database_mode="server", server_db_url=SERVER.replace(":5432", ":6543")).prepare()


def test_a_saved_choice_applies_its_secrets_are_sealed_and_the_environment_wins(tmp_path):
    config = settings(tmp_path).prepare()
    saved = choice.save(config, "supabase", url=SUPABASE, pools={"pool": 5, "overflow": 3, "dbos": 4})
    assert saved["mode"] == "supabase" and saved["pools"] == {"pool": 5, "overflow": 3, "dbos": 4}
    sealed = (tmp_path / choice.SEALED).read_bytes()
    plain = (tmp_path / mode.FILE).read_text()
    assert b"s3cret-pass" not in sealed and b"supabase.com" not in sealed, "the connection string is sealed"
    assert "s3cret" not in plain and "supabase" in json.loads(plain)["mode"], "the supervisor-readable file holds no secret"
    for name in (choice.SEALED, mode.FILE):
        assert stat.S_IMODE((tmp_path / name).stat().st_mode) == 0o600
    applied = settings(tmp_path).prepare()
    assert applied.database_mode == "supabase" and "supabase.com" in applied.database_url
    assert "axiom_pool=5" in applied.database_url and "axiom_overflow=3" in applied.database_url
    # DATABASE_MODE=local in .env is the way back, whatever was saved.
    back = settings(tmp_path, database_mode="local").prepare()
    assert back.database_mode == "local" and back.database_url.startswith("sqlite")
    # A string in the environment outranks a sealed one.
    other = settings(tmp_path, supabase_db_url=SUPABASE.replace("abc", "xyz")).prepare()
    assert "postgres.xyz" in other.database_url
    choice.save(config, "local")
    assert settings(tmp_path).prepare().database_mode == "local"


def test_switching_remembers_the_previous_choice_and_view_never_shows_a_password(tmp_path):
    config = settings(tmp_path).prepare()
    choice.save(config, "server", url=SERVER)
    choice.save(config, "local")
    view = choice.view(settings(tmp_path).prepare())
    assert view["previous"] == "server" and view["mode"] == "local"
    assert view["options"]["server"] == {"configured": True, "target": "axiom@db.example.org:5432/axiom"}
    assert "s3cret" not in json.dumps(view) and "s3cret" not in repr(settings(tmp_path, server_db_url=SERVER).prepare())


def test_the_screen_says_when_a_restart_is_needed(tmp_path):
    config = settings(tmp_path).prepare()
    assert choice.view(config)["restart_needed"] is False
    choice.save(config, "server", url=SERVER)
    view = choice.view(config)
    assert view["mode"] == "local" and view["chosen"] == "server" and view["restart_needed"] is True


def test_pools_follow_what_the_database_allows():
    assert choice.size_pools(None) == {"pool": 6, "overflow": 4, "dbos": 5}
    for limit in (6, 10, 15, 24, 60, 500):
        pools = choice.size_pools(limit)
        assert pools["pool"] + pools["overflow"] + pools["dbos"] <= max(limit - choice.RESERVE, 3)
        assert pools["pool"] >= 1 and pools["dbos"] >= 2


def test_a_bare_database_url_for_a_supabase_host_is_refused_because_it_would_use_public():
    with pytest.raises(RuntimeError, match="DATABASE_MODE=supabase"):
        Database(SUPABASE.replace("postgresql://", "postgresql+psycopg://"))


def test_native_tools_get_the_connection_in_the_environment_never_a_command_line(tmp_path):
    config = settings(tmp_path).prepare()
    assert choice.native_connection({}, tmp_path) is None
    choice.save(config, "supabase", url=SUPABASE)
    env = choice.native_connection({}, tmp_path)
    assert env["PGHOST"].endswith("supabase.com") and env["PGPASSWORD"] == "s3cret-pass" and env["PGOPTIONS"] == "-c search_path=axiom"
    assert env["PGSSLMODE"] == "require" and env["PGUSER"] == "postgres.abc"
    assert choice.native_connection({"DATABASE_MODE": "local"}, tmp_path) is None


def test_the_events_the_application_reads_back_to_decide_are_never_only_in_memory():
    # recovery.py and receipts.py take a "submitting" event as proof that a submit began; icims.py reads form_post to avoid reposting.
    assert {"submitting", "verification", "form_post"} <= DURABLE_EVENTS


def test_a_choice_that_is_not_one_of_the_three_is_an_error_with_the_way_back(tmp_path):
    (tmp_path / mode.FILE).write_text(json.dumps({"mode": "mongodb"}))
    with pytest.raises(DatabaseChoiceError, match="not one of local, supabase, server.*DATABASE_MODE=local"):
        settings(tmp_path).prepare()
