"""Move the application's data from the database it uses now to another one, and prove it arrived.

Generalises native/migrate.py, which does this from Docker, to any two PostgreSQL databases. The rules are the same:

* **The source is only read.** One REPEATABLE READ, READ ONLY snapshot serves every table, so the copy is one moment of the source.
  The only thing written there is the deployment hold, set first so that nothing new starts, and put back if the move fails.
* **The target changes in one transaction.** Tables are emptied (only with `replace`), filled, their sequences set and their row
  counts compared with the snapshot's before anything commits. A mismatch or an error rolls the whole thing back, so the target is
  either exactly the source or exactly what it was.
* **Nothing starts on a half-moved database.** The hold stays on until the copy has committed, the new choice is saved and the
  target says applications may resume. DBOS's workflow history is not copied: queued runs are enqueued again at every start, and a
  run that finished is not run again (workers.start), so history is not needed for safety.
* **Reversible.** The old database is untouched, and switching back is another switch (database.json remembers `previous`).
"""

import json
import os
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

import psycopg
from sqlalchemy.engine import make_url

from . import check, choice

HOLD = "execution_v2_deployment_hold"
ACTIVE = "('running','submitting')"  # `queued` is not in flight: it stays queued, and is enqueued again at start.
REPORT = "database-switch.json"
LOG = "database-switch.log"
LOCK = "database-switch.lock"


class SwitchError(RuntimeError):
    """The move did not happen, or could not be proved. The message says what is true now and what to do."""


@dataclass
class Endpoint:
    mode: str
    kwargs: dict
    schema: str
    app_url: str  # What jobpilot.db.Database takes for it.
    label: str = ""
    tables: list = field(default_factory=list)


def endpoint(config, mode: str, *, url: str = "") -> Endpoint:
    """Where `mode` is. Local is the URL this installation had before any remote one replaced it."""
    if mode == "local":
        raw = choice.local_urls(config)[0]
        if not raw.startswith("postgres"):
            raise SwitchError("The database on this computer is not PostgreSQL (this is a development setup), so there is nothing to copy to or from.")
        base, schema, app = raw, "public", raw
    else:
        base = choice.normalise(url or choice.url_for(config, mode), mode)
        pools = choice.pools_in_force(config, mode)
        app = make_url(base).update_query_dict(
            {"axiom_schema": choice.SCHEMA, "axiom_pool": str(pools["pool"]), "axiom_overflow": str(pools["overflow"])}
        ).render_as_string(hide_password=False)
        schema = choice.SCHEMA
    return Endpoint(mode, check.connect_kwargs(base), schema, app, choice.label(base))


def connect(point: Endpoint, **extra):
    return psycopg.connect(**{**point.kwargs, "application_name": "axiom-switch", **extra})


def q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


class Progress:
    """The move's state in a file the dashboard polls: where it is, what is done, and at the end what is true."""

    def __init__(self, config, say=print):
        self.folder = Path(config.data_dir) / "migration"
        self.folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.folder / REPORT
        self.say = say
        self.state = {"status": "starting", "phase": "", "started_at": time.time(), "tables": {}, "message": ""}

    def update(self, **values):
        self.state.update(values, updated_at=time.time())
        temporary = self.path.with_suffix(".tmp")
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(self.state, f)
        os.replace(temporary, self.path)

    def phase(self, text):
        self.say(text)
        self.update(phase=text)


def read_progress(config) -> dict:
    try:
        return json.loads((Path(config.data_dir) / "migration" / REPORT).read_text())
    except (OSError, ValueError):
        return {}


@contextmanager
def only_one(config):
    """One move at a time, and not beside another: the lock is held by the process that runs it."""
    import fcntl

    folder = Path(config.data_dir) / "migration"
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(folder / LOCK, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        raise SwitchError("A database move is already running.") from None
    try:
        yield
    finally:
        os.close(fd)


def running(config) -> bool:
    import fcntl

    path = Path(config.data_dir) / "migration" / LOCK
    if not path.exists():
        return False
    fd = os.open(path, os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return True
    finally:
        os.close(fd)
    return False


# ---- the hold ---------------------------------------------------------------------------------------------------------------


def read_hold(conn, schema):
    row = conn.execute(f"select value::text from {q(schema)}.settings where key = %s", (HOLD,)).fetchone()
    return row[0] if row else None


def write_hold(conn, schema, value: str | None):
    if value is None:
        conn.execute(f"delete from {q(schema)}.settings where key = %s", (HOLD,))
        return
    conn.execute(
        f"insert into {q(schema)}.settings (key, value) values (%s, %s::json) on conflict (key) do update set value = excluded.value",
        (HOLD, value),
    )


def hold_value(stage: str, reason: str) -> str:
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())
    return json.dumps({"at": stamp, "stage": stage, "reason": reason, "updated_at": stamp})


def in_flight(conn, schema) -> int:
    return conn.execute(f"select count(*) from {q(schema)}.runs where state in {ACTIVE}").fetchone()[0]


# ---- the target -------------------------------------------------------------------------------------------------------------


def prepare_target(target: Endpoint):
    """Create the app's tables in the target the way the app does at start (db.Database and the feature tables), and return their names."""
    from ..db import Database
    from ..features._store import create_tables

    db = Database(target.app_url)
    try:
        create_tables(db.engine)
        db.flush_events()
    finally:
        db.engine.dispose()
    with connect(target) as conn:
        names = [n for (n,) in conn.execute("select tablename from pg_tables where schemaname = %s order by 1", (target.schema,)).fetchall()]
    return names


def columns(conn, schema, table):
    return [c for (c,) in conn.execute(
        "select column_name from information_schema.columns where table_schema = %s and table_name = %s and is_generated = 'NEVER' "
        "order by ordinal_position", (schema, table)).fetchall()]


def count(conn, schema, table) -> int:
    return conn.execute(f"select count(*) from {q(schema)}.{q(table)}").fetchone()[0]


def fingerprint(conn, schema, table, cols) -> str:
    """A hash of every row's text, order-free: the same data gives the same number on any PostgreSQL 12 or newer."""
    row = ",".join(f"{q(c)}" for c in cols)
    return str(conn.execute(
        f"select coalesce(sum(hashtextextended(row({row})::text, 0)::numeric), 0) from {q(schema)}.{q(table)}"
    ).fetchone()[0])


def fix_sequences(conn, schema, table):
    for (column,) in conn.execute(
        "select column_name from information_schema.columns where table_schema = %s and table_name = %s "
        "and (is_identity = 'YES' or column_default like 'nextval(%%')", (schema, table)
    ).fetchall():
        conn.execute(
            f"select setval(pg_get_serial_sequence(%s, %s), coalesce(max({q(column)}), 1), max({q(column)}) is not null) from {q(schema)}.{q(table)}",
            (f"{q(schema)}.{q(table)}", column),
        )


# ---- the move ---------------------------------------------------------------------------------------------------------------


@contextmanager
def local_server(point: Endpoint):
    """The database on this computer, reachable. While a remote one is in use the supervisor does not run it, so a move to or from
    Local starts it for the length of the copy (the same cluster and settings the supervisor uses, native/pg.py) and stops it again,
    fast and clean, before the supervisor starts it for good."""
    try:
        connect(point, connect_timeout=3).close()
        yield
        return
    except psycopg.OperationalError:
        pass
    import signal
    import subprocess

    from ..native import env as native_env
    from ..native import pg

    try:
        cfg = native_env.prepare(native_env.load())
        bin_dir = pg.find_bin(cfg)
        pg.init_cluster(bin_dir, cfg)
    except Exception as exc:
        raise SwitchError(f"The database on this computer is not running and could not be started for the move: {exc}") from None
    log = os.open(cfg.logs / "postgres.log", os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    process = subprocess.Popen(pg.server_argv(bin_dir, cfg), env=pg.server_env(), stdin=subprocess.DEVNULL, stdout=log,
                               stderr=subprocess.STDOUT, start_new_session=True)
    os.close(log)
    try:
        deadline = time.monotonic() + 60
        while not pg.is_ready(bin_dir, cfg):
            if time.monotonic() > deadline or process.poll() is not None:
                raise SwitchError(f"The database on this computer did not start. See {cfg.logs / 'postgres.log'}")
            time.sleep(0.5)
        pg.ensure_database(bin_dir, cfg)
        yield
    finally:
        if process.poll() is None:
            process.send_signal(signal.SIGINT)
            try:
                process.wait(timeout=60)
            except subprocess.TimeoutExpired:
                process.send_signal(signal.SIGQUIT)
                process.wait()


def run(config, to: str, *, copy=True, replace=False, verify="counts", wait_seconds=900, say=print, url: str = "") -> dict:
    """Switch to `to`. With `copy` the data comes along (verified); without it the other database is used as it is.
    Returns the report. Raises SwitchError, and then the source is as it was and the choice unchanged."""
    if to not in choice.MODES:
        raise SwitchError(f"'{to}' is not a database choice.")
    current = config.database_mode
    if to == current:
        raise SwitchError(f"AXIOM already uses {LABELS[to]}.")
    with only_one(config):
        progress = Progress(config, say)
        try:
            report = _run(config, to, current, copy, replace, verify, wait_seconds, progress, url)
        except BaseException as exc:
            progress.update(status="failed", message=str(exc))
            raise
        progress.update(status="done", **report)
        return report


LABELS = {"local": "the database on this computer", "supabase": "Supabase", "server": "your server"}


def _run(config, to, current, copy, replace, verify, wait_seconds, progress, url):
    source, target = endpoint(config, current), endpoint(config, to, url=url)
    if (source.kwargs["host"], source.kwargs["port"], source.kwargs["dbname"], source.schema) == (
        target.kwargs["host"], target.kwargs["port"], target.kwargs["dbname"], target.schema
    ):
        raise SwitchError("Both choices point at the same database and schema.")
    progress.update(status="running", source=source.label, target=target.label, copy=copy, **{"from": current, "to": to})
    progress.phase(f"Testing {target.label} ...")
    tested = {"facts": {}}
    if to != "local":
        tested = check.run(to, choice.normalise(url or choice.url_for(config, to), to))
        if not tested["ok"]:
            failed = next(c for c in tested["checks"] if c["status"] == "fail")
            raise SwitchError(f"The new database failed its test ({failed['label']}): {failed['detail']} Nothing was changed.")
    pools = tested["facts"].get("pools")
    # The source gets its hold first: nothing new starts while the data moves, and nothing starts on a database that is not yet the choice.
    local = source if current == "local" else target if to == "local" else None
    with local_server(local) if local else _nothing():
        if copy:
            existing = tested["facts"].get("existing")
            if existing is None:  # Local was not tested like a remote one is: look at what it holds.
                with connect(target) as probe:
                    existing = check.existing_counts(probe.cursor(), target.schema)
            if existing.get("has_data") and not replace:
                counts = existing["counts"]
                raise SwitchError(
                    f"The new database already holds AXIOM data ({counts.get('jobs', 0):,} jobs, {counts.get('runs', 0):,} runs). Nothing was changed. "
                    "Switch without copying to use it as it is, or choose Replace to overwrite it with this installation's data."
                )
        source_conn = connect(source, autocommit=True)
        previous_hold, held = read_hold(source_conn, source.schema), False
        try:
            progress.phase("Holding new applications ...")
            write_hold(source_conn, source.schema, hold_value("draining", HOLD_REASON))
            held = True
            deadline = time.monotonic() + wait_seconds
            while (active := in_flight(source_conn, source.schema)):
                if time.monotonic() > deadline:
                    raise SwitchError(f"{active} application(s) were still running after {wait_seconds // 60} minutes. Nothing was changed; try again when they finish.")
                progress.phase(f"Waiting for {active} running application(s) to finish ...")
                time.sleep(3)
            if copy:
                progress.phase("Preparing the new database ...")
                target.tables = prepare_target(target)
                report = copy_all(source, target, replace, verify, progress)
            else:
                report = {"tables": {}, "message": f"Now using {target.label}. Nothing was copied."}
            progress.phase("Saving the choice ...")
            choice.save(config, to, url=url if to != "local" else "", pools=pools)
            held = False  # The source keeps its hold: nothing applies from it by accident. AXIOM releases it when it next starts on it.
            return report
        finally:
            try:
                if held:
                    write_hold(source_conn, source.schema, previous_hold)  # It failed: the source goes on as it was.
            finally:
                source_conn.close()


HOLD_REASON = "database switch"


@contextmanager
def _nothing():
    yield


def release_switch_hold(db) -> bool:
    """AXIOM's first start on a database it switched to (or back to): lift the hold the switch left, and only that hold."""
    value = db.get_setting(HOLD) or {}
    if value.get("stage") == "draining" and value.get("reason") == HOLD_REASON:
        db.set_setting(HOLD, json.loads(hold_value("complete", "database switch finished")))
        return True
    return False


def copy_all(source, target, replace, verify, progress):
    with connect(source) as src, connect(target) as tgt:
        src.isolation_level, src.read_only = psycopg.IsolationLevel.REPEATABLE_READ, True  # One moment of the source for every table.
        src_tables = [n for (n,) in src.execute("select tablename from pg_tables where schemaname = %s order by 1", (source.schema,)).fetchall()]
        wanted = [t for t in src_tables if t in target.tables]
        skipped = [t for t in src_tables if t not in target.tables]
        progress.update(tables={t: {"source": None, "target": 0} for t in wanted}, skipped=skipped)
        progress.phase("Counting what is there ...")
        before = {t: count(src, source.schema, t) for t in wanted}
        progress.update(tables={t: {"source": before[t], "target": 0} for t in wanted})
        tgt.execute("set local synchronous_commit = on")
        if replace:
            progress.phase("Clearing the new database's own copy ...")
            tgt.execute("truncate " + ", ".join(f"{q(target.schema)}.{q(t)}" for t in target.tables) + " restart identity")
        else:
            tgt.execute("delete from " + f"{q(target.schema)}.settings where key = 'control'")  # The default row the app writes on first start.
        done = {}
        for table in wanted:
            cols = [c for c in columns(src, source.schema, table) if c in columns(tgt, target.schema, table)]
            names = ", ".join(q(c) for c in cols)
            progress.phase(f"Copying {table} ({before[table]:,} rows) ...")
            moved = 0
            with src.cursor().copy(f"copy (select {names} from {q(source.schema)}.{q(table)}) to stdout") as out, \
                    tgt.cursor().copy(f"copy {q(target.schema)}.{q(table)} ({names}) from stdin") as into:
                for chunk in out:
                    into.write(chunk)
                    moved += len(chunk)
            fix_sequences(tgt, target.schema, table)
            done[table] = {"source": before[table], "target": count(tgt, target.schema, table), "bytes": moved}
            progress.update(tables={**progress.state["tables"], table: done[table]})
        progress.phase("Checking every table ...")
        differences = [f"{t}: {v['source']:,} rows in the old database, {v['target']:,} in the new one" for t, v in done.items() if v["source"] != v["target"]]
        if verify == "full" and not differences:
            for table in wanted:
                cols = [c for c in columns(src, source.schema, table) if c in columns(tgt, target.schema, table)]
                if fingerprint(src, source.schema, table, cols) != fingerprint(tgt, target.schema, table, cols):
                    differences.append(f"{table}: the rows differ although the counts match")
        if differences:
            tgt.rollback()
            raise SwitchError("The copy did not match, so nothing was kept: " + "; ".join(differences[:3]))
        tgt.commit()
        src.rollback()
    rows = sum(v["target"] for v in done.values())
    return {"tables": done, "skipped": skipped, "message": f"Verified: {len(done)} tables and {rows:,} rows match exactly."}
