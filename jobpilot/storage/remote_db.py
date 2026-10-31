"""What the database layer does differently when PostgreSQL is across a network (Supabase, or a server of your own).

Locally a transaction costs well under a millisecond and the application was written accordingly: production ran about
300 commits a second on 2026-10-30, half of them pool pings, from code that often runs on the one event loop that also
drives every browser. A trivial `with db.session()` is five round trips by default (ping, BEGIN, SELECT, COMMIT and the
ping's own transaction), so at 25 ms each a read blocks for 100 ms or more. Everything here removes round trips at the
choke point so call sites stay as they are, and all of it is active only for a remote database:

- A connection stays in autocommit until a statement that is not a plain read, which starts the transaction. A read-only
  session is then one round trip per SELECT. At READ COMMITTED each statement had its own snapshot anyway, so a read
  sees nothing different. Reads after a write in the same session are inside its transaction as before.
- The pool pings a connection only when it has been idle for a while, not at every checkout.
- Settings are served from memory for a moment and dropped the instant this process writes them.
- Events are written in batches by a thread, never on the caller's thread, except the few kinds the application reads back to
  decide whether it already submitted (db.DURABLE_EVENTS): those are on the server before the caller carries on.
- exclusive() queues waiters on a local lock, so they stop holding pooled connections while they wait for the lock.
"""

import asyncio
import atexit
import json
import logging
import os
import re
import sys
import threading
import time
from collections import Counter, deque

from sqlalchemy import event, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import DisconnectionError
from sqlalchemy.orm import Session

log = logging.getLogger("jobpilot.storage")

READ = re.compile(r"\s*(SELECT|SHOW)\b", re.I)
# A SELECT that must be inside a transaction: row locks and transaction-scoped advisory locks.
NEEDS_TRANSACTION = re.compile(r"\bFOR\s+(UPDATE|NO\s+KEY|SHARE|KEY)\b|pg_advisory_xact", re.I)
# A connection idle at most this long is trusted. A dropped socket still surfaces as an error on first use after that.
PING_IDLE_SECONDS = 20.0
RECYCLE_SECONDS = 1800
SETTING_TTL_SECONDS = 2.0
EVENT_FLUSH_SECONDS = 0.5
EVENT_BATCH = 200
EVENT_BACKLOG = 50_000
HOLD_SAMPLES = 2000
# Roles PostgREST serves requests as. A missing role (plain PostgreSQL, tests) has nothing to revoke.
API_ROLES = ("anon", "authenticated", "service_role")
PUBLIC_ROLES = ("anon", "authenticated")


def _private_schema_sql(schema):
    # The roles exist only on Supabase (and PostgREST setups). Nothing grants them this schema, but it must stay closed if something once did.
    return f"""
    CREATE SCHEMA IF NOT EXISTS "{schema}";
    REVOKE ALL ON SCHEMA "{schema}" FROM PUBLIC;
    DO $$ DECLARE role_name text; BEGIN
      FOREACH role_name IN ARRAY ARRAY{list(API_ROLES)!r} LOOP
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = role_name) THEN
          EXECUTE format('REVOKE ALL ON SCHEMA "{schema}" FROM %I', role_name);
          EXECUTE format('REVOKE ALL ON ALL TABLES IN SCHEMA "{schema}" FROM %I', role_name);
        END IF;
      END LOOP;
    END $$;
    """


def install(engine, schema):
    """Connection and pool behaviour for a remote engine. Call before the engine's first connection."""
    created = threading.Event()

    @event.listens_for(engine, "connect", insert=True)
    def on_connect(connection, _):
        # A SET, not a startup parameter: poolers differ on startup options, and a table that lands in `public` is
        # published to the anon key. check_private refuses to start if this ever did not take effect.
        before = connection.autocommit
        connection.autocommit = True
        with connection.cursor() as cursor:
            if not created.is_set():
                cursor.execute(_private_schema_sql(schema))
                cursor.execute(_private_schema_sql("dbos"))  # DBOS makes its tables here later; the schema is closed before it does.
                created.set()
            cursor.execute(f'SET search_path TO "{schema}"')
        connection.autocommit = before

    @event.listens_for(engine, "before_cursor_execute")
    def begin_on_write(conn, cursor, statement, parameters, context, executemany):
        dbapi = conn.connection.dbapi_connection
        if not dbapi.autocommit or conn.get_execution_options().get("single_statement"):
            return
        if not READ.match(statement) or NEEDS_TRANSACTION.search(statement):
            dbapi.autocommit = False  # psycopg sends BEGIN with the next statement.

    @event.listens_for(engine, "checkout")
    def ping_if_idle(connection, record, _):
        if time.monotonic() - record.info.get("used_at", 0) <= PING_IDLE_SECONDS:
            return
        try:
            connection.execute("SELECT 1")
        except Exception as exc:
            raise DisconnectionError(str(exc)) from exc  # The pool replaces it and tries again.

    @event.listens_for(engine, "checkin")
    def back_to_autocommit(connection, record):
        record.info["used_at"] = time.monotonic()
        if connection is None:
            return
        try:
            connection.autocommit = True
        except Exception:
            record.invalidate()  # Still inside a transaction it could not leave: never hand this one out again.


def exposure_problems(engine, schema, model_tables=(), other_schemas=("dbos",)) -> list[str]:
    """Is anything of ours reachable through Supabase's public API? Each problem is a sentence; [] means no.

    The API serves a schema only if it is listed under Exposed schemas and the anon role may use it; tables in `public`
    are served by default. So: none of our tables in `public`, and no USAGE for the API's roles on our schemas.
    """
    found = []
    with engine.connect() as c:
        current = c.execute(text("select current_schema()")).scalar()
        if current != schema:
            found.append(f"Connections use schema {current!r} instead of {schema!r}.")
        names = list(model_tables)
        if names:
            public = c.execute(
                text("select tablename from pg_tables where schemaname = 'public' and tablename = any(:names)"),
                {"names": names},
            ).scalars().all()
            if public:
                found.append("These tables are in the public schema, which the anon key can read: " + ", ".join(sorted(public)))
        roles = c.execute(
            text("select rolname from pg_roles where rolname = any(:roles)"), {"roles": list(PUBLIC_ROLES)}
        ).scalars().all()
        schemas = c.execute(
            text("select nspname from pg_namespace where nspname = any(:names)"),
            {"names": [schema, *other_schemas]},
        ).scalars().all()
        for role in roles:
            for name in schemas:
                if c.execute(text("select has_schema_privilege(:r, :s, 'USAGE')"), {"r": role, "s": name}).scalar():
                    found.append(f"The {role} role can use the {name} schema.")
    return found


def seal_schema(engine, schema):
    """Close a schema another component created (DBOS creates `dbos`) to the API's roles."""
    raw = engine.raw_connection()
    try:
        with raw.cursor() as cursor:  # Not exec_driver_sql: its parameter pass would read the `%I` of format() as a placeholder.
            cursor.execute(_private_schema_sql(schema))
        raw.commit()
    finally:
        raw.close()


class SettingCache:
    """Settings for a moment in memory. Values are kept as text, so no caller can change what another one reads."""

    MISSING = object()

    def __init__(self, ttl=SETTING_TTL_SECONDS):
        self.ttl, self.rows, self.generation, self.lock = ttl, {}, 0, threading.Lock()

    def get(self, key):
        with self.lock:
            found = self.rows.get(key)
        if not found or time.monotonic() > found[0]:
            return self.MISSING
        return None if found[1] is None else json.loads(found[1])

    def begin_read(self):
        return self.generation

    def store(self, key, value, generation):
        text_value = None if value is None else json.dumps(value)
        with self.lock:
            # A write since the read began may not be in `value`, so it is not remembered.
            if generation == self.generation:
                self.rows[key] = (time.monotonic() + self.ttl, text_value)

    def invalidate(self, keys=None):
        with self.lock:
            self.generation += 1
            if keys is None:
                self.rows.clear()
            else:
                for key in keys:
                    self.rows.pop(key, None)


class EventWriter:
    """Run events in batches off the caller's thread. An event is at most EVENT_FLUSH_SECONDS from the database, and
    the state a run depends on is never only here: runs and receipts are written in their own transactions."""

    def __init__(self, engine, model, interval=EVENT_FLUSH_SECONDS):
        self.engine, self.model, self.interval = engine, model, interval
        self.rows, self.guard, self.flushing = deque(), threading.Lock(), threading.Lock()
        self.wake, self.thread, self.dropped = threading.Event(), None, 0
        atexit.register(self.flush)

    def add(self, run_id, kind, message, data, created_at):
        with self.guard:
            self.rows.append(
                {"run_id": run_id, "kind": kind, "message": message, "data": data or {}, "created_at": created_at}
            )
            while len(self.rows) > EVENT_BACKLOG:  # The database is away and memory is not unlimited.
                self.rows.popleft()
                self.dropped += 1
            if self.thread is None:
                self.thread = threading.Thread(target=self._run, name="event-writer", daemon=True)
                self.thread.start()
        if len(self.rows) >= EVENT_BATCH:
            self.wake.set()

    def _run(self):
        while True:
            self.wake.wait(self.interval)
            self.wake.clear()
            try:
                self.flush()
            except Exception:
                log.warning("Could not write %d events yet", len(self.rows), exc_info=True)
                time.sleep(self.interval * 4)

    def flush(self):
        """Write everything waiting, in order. Rows stay queued if the write fails."""
        with self.flushing:
            while True:
                with self.guard:
                    batch = [self.rows[i] for i in range(min(EVENT_BATCH, len(self.rows)))]
                if not batch:
                    return
                with self.engine.connect().execution_options(single_statement=True) as c:
                    c.execute(self.model.__table__.insert().values(batch))
                    c.commit()
                with self.guard:
                    for _ in batch:
                        self.rows.popleft()


class StatementAudit:
    """How long statements take and which ones run on the event loop, where they block every browser."""

    def __init__(self, engine):
        self.started = time.monotonic()
        self.times, self.holds = deque(maxlen=HOLD_SAMPLES), deque(maxlen=HOLD_SAMPLES)
        self.sites, self.statements, self.on_loop = Counter(), 0, 0
        self.loop_seconds = 0.0

        @event.listens_for(engine, "before_cursor_execute")
        def before(conn, cursor, statement, parameters, context, executemany):
            if context is not None:
                context._audit_at = time.perf_counter()

        @event.listens_for(engine, "after_cursor_execute")
        def after(conn, cursor, statement, parameters, context, executemany):
            if context is None or not hasattr(context, "_audit_at"):
                return
            elapsed = time.perf_counter() - context._audit_at
            self.statements += 1
            self.times.append(elapsed)
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                return
            self.on_loop += 1
            self.loop_seconds += elapsed
            self.sites[_caller()] += 1

    def hold(self, seconds):
        self.holds.append(seconds)

    def snapshot(self):
        minutes = max((time.monotonic() - self.started) / 60, 1 / 60)
        return {
            "statements_per_minute": round(self.statements / minutes),
            "statement_p50_ms": percentile(self.times, 50),
            "statement_p95_ms": percentile(self.times, 95),
            "exclusive_hold_p95_ms": percentile(self.holds, 95),
            "on_loop_per_minute": round(self.on_loop / minutes),
            "on_loop_seconds_per_minute": round(self.loop_seconds / minutes, 2),
            "on_loop_sites": [{"site": site, "count": count} for site, count in self.sites.most_common(15)],
        }


def percentile(samples, share):
    values = sorted(samples)
    return round(values[min(len(values) - 1, int(len(values) * share / 100))] * 1000, 1) if values else None


def _caller():
    """The first frame in this application that is not the database layer."""
    frame = sys._getframe(2)
    while frame:
        name = frame.f_code.co_filename
        if os.sep + "jobpilot" + os.sep in name and not name.endswith((os.sep + "db.py", "remote_db.py")):
            return f"{name.rsplit(os.sep + 'jobpilot' + os.sep, 1)[-1]}:{frame.f_code.co_name}"
        frame = frame.f_back
    return "unknown"


def install_session_hooks(database, session_factory, setting_model, event_model):
    """Keep the two in-memory shortcuts honest for every write and read that goes through a session."""

    @event.listens_for(session_factory, "after_flush")
    def written(session, _):
        keys = {o.key for o in (*session.new, *session.dirty, *session.deleted) if isinstance(o, setting_model)}
        if keys:
            session.info.setdefault("setting_keys", set()).update(keys)
            database.settings_cache.invalidate(keys)

    @event.listens_for(session_factory, "after_commit")
    def committed(session):
        # Again after the commit: a read between the flush and the commit saw the old value and may have stored it.
        if keys := session.info.pop("setting_keys", None):
            database.settings_cache.invalidate(keys)

    @event.listens_for(session_factory, "do_orm_execute")
    def executing(state):
        mappers = {m.class_ for m in state.all_mappers}
        if setting_model in mappers and not state.is_select:
            database.settings_cache.invalidate()  # A bulk UPDATE or DELETE names no keys.
        if event_model in mappers:
            database.events.flush()  # A read of a run's events sees everything already emitted.


def single_statement_upsert(engine, model, key, value):
    """INSERT .. ON CONFLICT in one round trip, its own transaction."""
    statement = pg_insert(model).values(key=key, value=value)
    statement = statement.on_conflict_do_update(index_elements=[model.key], set_={"value": statement.excluded.value})
    with engine.connect().execution_options(single_statement=True) as c:
        c.execute(statement)
        c.commit()


def read_setting(engine, model, key):
    with engine.connect() as c:
        return c.execute(select(model.value).where(model.key == key)).scalar()


__all__ = ["EventWriter", "SettingCache", "Session", "StatementAudit", "exposure_problems", "install", "seal_schema"]
