"""Switching databases copies, proves it arrived, never deletes the source and can go back (2026-10-31).

Between two scratch databases: `src` plays the database on this computer, `dst` a server of the owner's.
"""

import json

import pytest

from database_support import scratch_pg  # noqa: F401 - the fixture
from jobpilot.config import Settings
from jobpilot.db import Database, Event, Job, Run
from jobpilot.features._store import create_tables
from jobpilot.storage import choice, mode, switch


def populate(url):
    db = Database(url)
    create_tables(db.engine)
    with db.session() as s:
        for i in range(40):
            s.add(Job(id=f"job{i:03d}", identity=f"id{i}", url=f"https://example.test/{i}", title=f"Title {i}", demo=True))
        for i in range(25):
            s.add(Run(id=f"run{i:03d}", job_id=f"job{i:03d}", state="confirmed", packet={"note": "x" * 40}))
        for i in range(120):
            s.add(Event(run_id="run001", kind="step", message=f"m{i}", data={"i": i}))
    db.set_setting("owner_note", {"keep": [1, 2, 3]})
    db.engine.dispose()


@pytest.fixture
def pair(scratch_pg, tmp_path):
    src, dst = scratch_pg.create("src"), scratch_pg.create("dst")
    local = scratch_pg.url(src, scheme="postgresql+psycopg")
    populate(local)
    config = Settings(_env_file=None, data_dir=tmp_path, app_token="t", database_url=local, dbos_database_url=local,
                      server_db_url=scratch_pg.url(dst)).prepare()
    return scratch_pg, src, dst, config, tmp_path


def snapshot(scratch, database, schema, skip=("settings",)):
    tables = [t for (t,) in scratch.sql(database, "select tablename from pg_tables where schemaname = %s order by 1", (schema,))]
    return {
        t: scratch.sql(database, f'select count(*), coalesce(sum(hashtextextended(x::text, 0)::numeric), 0) from "{schema}"."{t}" x')[0]
        for t in tables if t not in skip
    }


def test_the_data_arrives_verified_and_the_source_is_untouched(pair):
    scratch, src, dst, config, data = pair
    before = snapshot(scratch, src, "public")
    report = switch.run(config, "server", verify="full", say=lambda _: None)
    assert report["message"].startswith("Verified:") and report["tables"]["jobs"] == {"source": 40, "target": 40, "bytes": report["tables"]["jobs"]["bytes"]}
    assert snapshot(scratch, dst, "axiom") == before, "every table, row for row"
    assert snapshot(scratch, src, "public") == before, "the source was only read"
    # Sequences continue after the copied rows instead of colliding with them.
    assert scratch.sql(dst, "select nextval(pg_get_serial_sequence('axiom.events', 'id'))")[0][0] == 121
    assert scratch.sql(dst, "select value->>'keep' from axiom.settings where key = 'owner_note'")[0][0] == "[1, 2, 3]"
    # The choice is saved for the next start, with the connection sealed.
    saved = mode.read(data)
    assert saved["mode"] == "server" and saved["previous"] == "local" and saved["pools"]["pool"] >= 1
    assert b"scratchpw" not in (data / choice.SEALED).read_bytes()
    assert "scratchpw" not in (data / "migration" / switch.REPORT).read_text(), "no password in the progress file"


def test_new_applications_are_held_while_it_moves_and_the_hold_follows_the_data(pair):
    scratch, src, dst, config, _ = pair
    switch.run(config, "server", say=lambda _: None)
    for database, schema in ((src, "public"), (dst, "axiom")):
        hold = scratch.sql(database, f"select value::json->>'stage', value::json->>'reason' from {schema}.settings where key = 'execution_v2_deployment_hold'")
        assert hold == [("draining", switch.HOLD_REASON)], "nothing applies from either database until AXIOM starts on the new one"
    db = Database(scratch.url(dst, scheme="postgresql+psycopg", query="?axiom_schema=axiom&axiom_pool=2&axiom_overflow=0"))
    assert switch.release_switch_hold(db) is True and db.get_setting(switch.HOLD)["stage"] == "complete"
    db.engine.dispose()


def test_only_the_hold_the_switch_left_is_ever_lifted(tmp_path):
    db = Database(f"sqlite:///{tmp_path}/a.db")
    db.set_setting(switch.HOLD, {"stage": "draining", "reason": "held with axiom hold"})
    assert switch.release_switch_hold(db) is False and db.get_setting(switch.HOLD)["stage"] == "draining"
    db.set_setting(switch.HOLD, json.loads(switch.hold_value("draining", switch.HOLD_REASON)))
    assert switch.release_switch_hold(db) is True and db.get_setting(switch.HOLD)["stage"] == "complete"


def test_data_already_in_the_target_is_never_overwritten_without_being_asked(pair):
    scratch, src, dst, config, data = pair
    switch.run(config, "server", say=lambda _: None)
    # Back again: the old database still holds the old copy, so copying over it needs Replace.
    back = Settings(_env_file=None, data_dir=data, app_token="t", database_mode="server", server_db_url=scratch.url(dst),
                    database_url=scratch.url(src, scheme="postgresql+psycopg"), dbos_database_url="x").prepare()
    with pytest.raises(switch.SwitchError, match=r"already holds AXIOM data.*Nothing was changed"):
        switch.run(back, "local", say=lambda _: None)
    assert scratch.sql(src, "select count(*) from public.jobs")[0][0] == 40
    with Database(scratch.url(dst, scheme="postgresql+psycopg", query="?axiom_schema=axiom&axiom_pool=2&axiom_overflow=0")).session() as s:
        s.add(Job(id="job999", identity="new", url="https://example.test/new", demo=True))  # Work done on the server meanwhile.
    switch.run(back, "local", replace=True, say=lambda _: None)
    assert scratch.sql(src, "select count(*) from public.jobs")[0][0] == 41, "going back brings the newer work with it"
    assert mode.read(data)["mode"] == "local" and mode.read(data)["previous"] == "server"


def test_a_copy_that_does_not_match_keeps_nothing_and_puts_everything_back(pair, monkeypatch):
    scratch, src, dst, config, data = pair
    real = switch.count

    def lie(conn, schema, table):
        return real(conn, schema, table) + (1 if schema == "axiom" and table == "jobs" else 0)

    monkeypatch.setattr(switch, "count", lie)
    with pytest.raises(switch.SwitchError, match=r"did not match.*jobs"):
        switch.run(config, "server", say=lambda _: None)
    assert scratch.sql(dst, "select count(*) from axiom.jobs")[0][0] == 0, "the target is as it was"
    assert not (data / mode.FILE).exists(), "the choice was not changed"
    assert scratch.sql(src, "select count(*) from public.settings where key = 'execution_v2_deployment_hold'")[0][0] == 0, "the source's hold is gone again"
    assert json.loads((data / "migration" / switch.REPORT).read_text())["status"] == "failed"


def test_it_waits_for_running_applications_and_gives_up_without_changing_anything(pair):
    scratch, src, dst, config, data = pair
    scratch.sql(src, "update public.runs set state = 'running' where id = 'run001'")
    with pytest.raises(switch.SwitchError, match=r"1 application\(s\) were still running"):
        switch.run(config, "server", wait_seconds=1, say=lambda _: None)
    assert scratch.sql(src, "select state from public.runs where id = 'run001'")[0][0] == "running"
    assert scratch.sql(src, "select count(*) from public.settings where key = 'execution_v2_deployment_hold'")[0][0] == 0
    assert scratch.sql(dst, "select count(*) from pg_tables where schemaname = 'axiom'")[0][0] == 0, "not even a table was made"
    assert not (data / mode.FILE).exists()


def test_a_target_that_fails_its_test_is_refused_before_the_source_is_touched(pair):
    scratch, src, dst, config, data = pair
    scratch.sql(dst, "create schema axiom")
    scratch.sql(dst, "grant usage on schema axiom to anon")
    with pytest.raises(switch.SwitchError, match=r"failed its test \(Closed to the public API\).*anon role"):
        switch.run(config, "server", say=lambda _: None)
    assert scratch.sql(src, "select count(*) from public.settings where key = 'execution_v2_deployment_hold'")[0][0] == 0


def test_using_the_other_database_as_it_is_copies_nothing(pair):
    scratch, src, dst, config, data = pair
    report = switch.run(config, "server", copy=False, say=lambda _: None)
    assert report["message"].startswith("Now using") and mode.read(data)["mode"] == "server"
    assert scratch.sql(dst, "select count(*) from pg_tables where schemaname = 'axiom'")[0][0] == 0


def test_two_moves_cannot_run_at_once(pair):
    config = pair[3]
    with switch.only_one(config):
        assert switch.running(config) is True
        with pytest.raises(switch.SwitchError, match="already running"):
            with switch.only_one(config):
                pass
    assert switch.running(config) is False


def test_a_development_database_that_is_not_postgresql_cannot_be_moved(tmp_path):
    config = Settings(_env_file=None, data_dir=tmp_path, app_token="t", server_db_url="postgresql://u:p@127.0.0.1:1/x").prepare()
    with pytest.raises(switch.SwitchError, match="not PostgreSQL"):
        switch.run(config, "server", say=lambda _: None)
