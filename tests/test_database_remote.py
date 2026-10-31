"""The database layer over a network keeps every commit and duplicate protection the local one has (2026-10-31).

Runs against a scratch PostgreSQL whose own synchronous_commit is off, which is how a server of the owner's could be set.
"""

import threading
import time

import pytest
from sqlalchemy import select

from database_support import scratch_pg  # noqa: F401 - the fixture
from jobpilot.db import Database, Event, Job, Run, Setting
from jobpilot.storage import remote_db


@pytest.fixture
def remote(scratch_pg):
    name = scratch_pg.create("remote")
    url = scratch_pg.url(name, scheme="postgresql+psycopg", query="?axiom_schema=axiom&axiom_pool=4&axiom_overflow=2")
    db = Database(url)
    yield db, scratch_pg, name
    db.flush_events()
    db.engine.dispose()


def count(scratch, name, table, where=""):
    return scratch.sql(name, f"select count(*) from axiom.{table} {where}")[0][0]


def test_every_table_is_in_the_private_schema_and_none_in_public(remote):
    db, scratch, name = remote
    assert db.remote and count(scratch, name, "runs") == 0
    assert scratch.sql(name, "select count(*) from pg_tables where schemaname = 'public'")[0][0] == 0
    assert scratch.sql(name, "select has_schema_privilege('anon', 'axiom', 'USAGE')")[0][0] is False


def test_a_commit_is_on_the_server_and_a_failure_leaves_nothing(remote):
    db, scratch, name = remote
    with db.session() as s:
        s.add(Run(id="r1", job_id="j", state="queued"))
    assert count(scratch, name, "runs") == 1, "visible to another connection at once"
    with pytest.raises(RuntimeError):
        with db.session() as s:
            s.add(Run(id="r2", job_id="j", state="queued"))
            raise RuntimeError("boom")
    with pytest.raises(RuntimeError):
        with db.exclusive() as s:
            s.add(Run(id="r3", job_id="j", state="queued"))
            raise RuntimeError("boom")
    assert count(scratch, name, "runs") == 1
    with db.session() as s:
        assert s.get(Run, "r2") is None and s.get(Run, "r3") is None


def test_exclusive_is_durable_whatever_the_servers_own_commit_setting(remote):
    """The reservation that says `submitting` must be flushed before the click, even on a server with synchronous_commit off."""
    db, scratch, name = remote
    assert scratch.sql(name, "show synchronous_commit")[0][0] == "off"
    with db.session() as s:
        s.add(Run(id="r1", job_id="j", state="running", mode="submit"))
    with db.exclusive() as s:
        assert s.connection().exec_driver_sql("show synchronous_commit").scalar() == "on"
        s.get(Run, "r1").state = "submitting"
    assert scratch.sql(name, "select state from axiom.runs where id = 'r1'")[0][0] == "submitting"


def test_exclusive_still_excludes_another_process(remote):
    """Two Database objects stand for two processes: the in-process queue is not what keeps them apart, the advisory lock is."""
    db, scratch, name = remote
    other = Database(scratch.url(name, scheme="postgresql+psycopg", query="?axiom_schema=axiom&axiom_pool=3&axiom_overflow=0"))
    spans, guard = [], threading.Lock()

    def hold(handle):
        for _ in range(3):
            with handle.exclusive():
                began = time.monotonic()
                time.sleep(0.05)
                with guard:
                    spans.append((began, time.monotonic()))

    threads = [threading.Thread(target=hold, args=(h,)) for h in (db, other)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    other.engine.dispose()
    spans.sort()
    assert len(spans) == 6 and all(a[1] <= b[0] + 0.005 for a, b in zip(spans, spans[1:])), "no two overlapped"


def test_the_event_that_proves_a_submit_began_is_on_the_server_before_the_caller_goes_on(remote):
    """recovery.py and receipts.py read a `submitting` event as proof that the click happened. Batched telemetry before it must
    arrive first and in order, and nothing may be left behind a crash."""
    db, scratch, name = remote
    db.event("r1", "step", "filled the form")
    db.event("r1", "submitting", "Commit step started")
    kinds = [k for (k,) in scratch.sql(name, "select kind from axiom.events order by id")]
    assert kinds == ["step", "submitting"]


def test_other_events_are_batched_off_the_callers_thread_and_flushed_on_read(remote):
    db, scratch, name = remote
    began = time.perf_counter()
    for i in range(50):
        db.event("r1", "field_timing", f"f{i}")
    assert time.perf_counter() - began < 0.5, "emitting never waits for the network"
    with db.session() as s:  # A read of events sees everything already emitted.
        assert len(s.scalars(select(Event).where(Event.run_id == "r1")).all()) == 50
    assert count(scratch, name, "events") == 50


def test_settings_are_remembered_for_a_moment_and_forgotten_the_instant_this_process_writes_them(remote):
    db, scratch, name = remote
    db.set_setting("k", {"v": 1})
    assert db.get_setting("k") == {"v": 1}
    scratch.sql(name, "update axiom.settings set value = '{\"v\": 2}' where key = 'k'")
    assert db.get_setting("k") == {"v": 1}, "served from memory for a moment"
    db.set_setting("k", {"v": 3})
    assert db.get_setting("k") == {"v": 3}, "this process's own write is never hidden by it"
    assert db.get_setting("missing", "dflt") == "dflt"


def test_a_grant_to_the_public_api_roles_is_undone_at_start_and_a_table_in_public_is_refused(scratch_pg):
    name = scratch_pg.create("exposed")
    url = scratch_pg.url(name, scheme="postgresql+psycopg", query="?axiom_schema=axiom&axiom_pool=2&axiom_overflow=0")
    Database(url).engine.dispose()
    scratch_pg.sql(name, "grant usage on schema axiom to anon")
    Database(url).engine.dispose()  # Every start closes the schema again.
    assert scratch_pg.sql(name, "select has_schema_privilege('anon', 'axiom', 'USAGE')")[0][0] is False
    scratch_pg.sql(name, "create table public.runs (id text)")  # Something this layer cannot close: the anon key reads `public`.
    with pytest.raises(RuntimeError, match=r"Refusing to start.*public schema.*runs"):
        Database(url)
    # DBOS's schema is closed at start too, and again once DBOS has made its tables in it.
    scratch_pg.sql(name, "drop table public.runs; grant usage on schema dbos to authenticated")
    engine = Database(url).engine
    assert scratch_pg.sql(name, "select has_schema_privilege('authenticated', 'dbos', 'USAGE')")[0][0] is False
    scratch_pg.sql(name, "create table dbos.workflow (id text); grant select on dbos.workflow to anon")
    remote_db.seal_schema(engine, "dbos")
    assert scratch_pg.sql(name, "select has_table_privilege('anon', 'dbos.workflow', 'SELECT')")[0][0] is False
    engine.dispose()


def test_a_connection_the_server_dropped_while_idle_is_replaced_not_surfaced(remote, monkeypatch):
    db, scratch, name = remote
    monkeypatch.setattr(remote_db, "PING_IDLE_SECONDS", 0.0)
    with db.session() as s:
        s.add(Job(id="j1", identity="x", url="u"))
    scratch.sql("postgres", f"select pg_terminate_backend(pid) from pg_stat_activity where datname = '{name}' and pid <> pg_backend_pid()")
    with db.session() as s:
        assert s.get(Job, "j1") is not None


def test_the_statement_audit_counts_what_runs_on_the_event_loop(remote):
    import asyncio

    db, _, _ = remote

    async def on_loop():
        db.get_setting("anything")  # A synchronous round trip on the loop, as much of the application does.

    asyncio.run(on_loop())
    snapshot = db.audit.snapshot()
    assert snapshot["on_loop_per_minute"] > 0 and snapshot["on_loop_sites"]
    assert db.get_setting("anything") is None


def test_dbos_gets_a_small_pool_and_no_listeners_when_remote(remote, tmp_path):
    from jobpilot.config import Settings
    from jobpilot.workers import dbos_limits

    db, _, _ = remote
    config = Settings(_env_file=None, data_dir=tmp_path, app_token="t", database_mode="server", dbos_pool_size=None)
    assert dbos_limits(config, db) == {"sys_db_pool_size": 5, "use_listen_notify": False}
    assert dbos_limits(config, type("Local", (), {"remote": False})()) == {}


def test_an_idle_outbox_poll_is_one_read_and_a_due_row_is_still_claimed_once(remote):
    """Four pollers ask every second; on a 30 ms link the locked claim was three quarters of what ran on the event loop (2026-10-31)."""
    from jobpilot import outbox
    from jobpilot.db import Outbox

    db, _, _ = remote
    before = db.audit.statements
    assert outbox.claim(db, ["notify"]) is None
    assert db.audit.statements - before == 1, "no lock, no transaction"
    with db.session() as s:
        s.add(Outbox(id="o1", kind="notify", state="pending", available_at=0))
    first = outbox.claim(db, ["notify"])
    assert first and first["id"] == "o1" and first["state"] == "processing"
    assert outbox.claim(db, ["notify"]) is None, "the row is leased, so the second poller gets nothing"


def test_off_loop_runs_remote_work_in_a_thread_and_local_work_inline(remote):
    import asyncio
    import threading

    db, _, _ = remote

    async def where(handle):
        return await handle.off_loop(threading.get_ident), threading.get_ident()

    ran, loop_thread = asyncio.run(where(db))
    assert ran != loop_thread
    local = type("Local", (Database,), {"__init__": lambda self: None})()
    local.remote = False
    ran, loop_thread = asyncio.run(where(local))
    assert ran == loop_thread
