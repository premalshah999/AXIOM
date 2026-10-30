"""Moving a Docker installation here: Docker is only read, the disk is never filled, and every step can run again."""

import hashlib
import json
import os
import re
import subprocess
import time
from pathlib import Path

import pytest

from jobpilot.native import migrate, pg, services
from jobpilot.native.env import NativeError, load, local_cdp, prepare, read_dotenv
from jobpilot.native.migrate import Source

ROOT = Path(__file__).resolve().parent.parent
MODULE = ROOT / "jobpilot" / "native" / "migrate.py"


class LocalVolume(Source):
    """The Docker volume, as a folder: the same tar stream, with no Docker."""

    def __init__(self, folder):
        super().__init__("example-db-1", "example_applicant-data", "postgres:example")
        self.folder = folder

    def listing(self):
        files = {}
        for path in self.folder.rglob("*"):
            relative = path.relative_to(self.folder).as_posix()
            if path.is_file() and not migrate.SKIP_NAMES.intersection(relative.split("/")):
                stat = path.stat()
                files[relative] = (stat.st_size, stat.st_mtime)
        return files

    def tar_argv(self):
        return ["tar", "-C", str(self.folder), "-cf", "-", "--null", "-T", "-"]

    def hashes(self, paths):
        return {p: hashlib.sha256((self.folder / p).read_bytes()).hexdigest() for p in paths}


def write(path, data=b"x", mode=None, mtime=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data if isinstance(data, bytes) else data.encode())
    if mode:
        path.chmod(mode)
    if mtime:
        os.utime(path, (mtime, mtime))


@pytest.fixture
def volume(tmp_path):
    folder = tmp_path / "volume"
    write(folder / "mail-key", b"synthetic-key", 0o600)
    write(folder / "access-token", b"synthetic-token", 0o600)
    write(folder / "resumes" / "abc.pdf", b"%PDF synthetic", 0o600)
    write(folder / "__pycache__" / "x.pyc", b"skip")
    base = time.time() - 10000
    for n in range(6):
        write(folder / "runs" / f"run{n}" / "steps" / "01.html", b"<html>" * 500, mtime=base + n)
        write(folder / "runs" / f"run{n}" / "final.png", b"\x89PNG" * 250, mtime=base + n)
    return folder


@pytest.fixture
def cfg(tmp_path):
    return prepare(load(tmp_path, environ={"POSTGRES_PASSWORD": "synthetic", "DATA_DIR": str(tmp_path / "data")}))


def quiet(_):
    pass


def test_everything_arrives_with_its_permissions_and_a_report(cfg, volume):
    assert migrate.files(cfg, LocalVolume(volume), keep_free_gb=0, say=quiet) == 0
    assert (cfg.data_dir / "mail-key").read_bytes() == b"synthetic-key"
    assert (cfg.data_dir / "mail-key").stat().st_mode & 0o077 == 0, "the mail key must stay private"
    assert (cfg.data_dir / "runs" / "run3" / "final.png").exists()
    assert not (cfg.data_dir / "__pycache__").exists()
    assert (cfg.data_dir / "runs").stat().st_mode & 0o077 == 0
    report = json.loads((cfg.migration / "files-report.json").read_text())
    assert report["complete"] and report["left_files"] == 0 and report["sampled"]
    assert not (cfg.migration / "incoming").exists()


def test_file_times_survive_the_compressed_copy(cfg, volume):
    """A later pass decides what changed from the times, so the copy must keep them."""
    migrate.files(cfg, LocalVolume(volume), keep_free_gb=0, say=quiet)
    source = volume / "runs" / "run1" / "final.png"
    assert abs((cfg.data_dir / "runs" / "run1" / "final.png").stat().st_mtime - source.stat().st_mtime) < 1


def test_keys_and_documents_come_first_then_the_newest_runs_whole(cfg, volume, monkeypatch):
    monkeypatch.setattr(migrate, "BATCH_FILES", 4)
    plan = migrate.plan_files(cfg, LocalVolume(volume).listing())
    assert {path for path, _ in plan.batches[0]} >= {"mail-key", "access-token", "resumes/abc.pdf"}
    order = []
    for batch in plan.batches:
        for path, _ in batch:
            run = migrate.group_of(path)
            if run and run not in order:
                order.append(run)
    assert order == [f"run{n}" for n in (5, 4, 3, 2, 1, 0)]
    for batch in plan.batches:
        runs = {migrate.group_of(p) for p, _ in batch} - {""}
        for run in runs:
            assert sum(migrate.group_of(p) == run for p, _ in batch) == 2, "a run moves whole"


def test_the_floor_stops_the_move_and_a_later_run_finishes_it(cfg, volume, monkeypatch):
    """The disk has been filled by a copy loop before (2026-10-21)."""
    monkeypatch.setattr(migrate, "BATCH_FILES", 2)
    room = {"left": migrate.GIB + 6000}

    def free(_):
        return room["left"]

    monkeypatch.setattr(migrate, "free_bytes", free)
    moved = []
    real = migrate.fetch_batch

    def fetch(cfg, source, batch, incoming):
        moved.append(batch)
        real(cfg, source, batch, incoming)
        room["left"] -= sum(s for _, s in batch)

    monkeypatch.setattr(migrate, "fetch_batch", fetch)
    code = migrate.files(cfg, LocalVolume(volume), keep_free_gb=1, sample=0, say=quiet)
    assert code == 3, "stopped at the floor: not an error, not complete"
    assert room["left"] >= migrate.GIB, "the floor held"
    report = json.loads((cfg.migration / "files-report.json").read_text())
    assert report["stopped_at_floor"] and not report["complete"] and report["left_runs"]
    assert (cfg.data_dir / "mail-key").exists(), "the keys come first"
    assert (cfg.data_dir / "runs" / "run5" / "final.png").exists(), "the newest runs next"
    assert not (cfg.data_dir / "runs" / "run0").exists(), "the oldest wait"
    before = sum(len(b) for b in moved)
    room["left"] = 10 * migrate.GIB
    assert migrate.files(cfg, LocalVolume(volume), keep_free_gb=1, sample=0, say=quiet) == 0
    fetched_again = {p for b in moved[:1] for p, _ in b} & {p for b in moved[1:] for p, _ in b}
    assert before < sum(len(b) for b in moved)
    assert not fetched_again, "what arrived is not fetched twice"
    assert (cfg.data_dir / "runs" / "run0" / "final.png").exists()


def test_a_second_pass_fetches_only_what_changed_in_docker(cfg, volume):
    migrate.files(cfg, LocalVolume(volume), keep_free_gb=0, say=quiet)
    write(volume / "runs" / "run9" / "new.json", b"{}")
    write(volume / "runs" / "run1" / "events.jsonl", b"one\n", mtime=time.time() - 100)
    migrate.files(cfg, LocalVolume(volume), keep_free_gb=0, say=quiet)
    write(volume / "runs" / "run1" / "events.jsonl", b"one\ntwo\n", mtime=time.time())
    plan = migrate.plan_files(cfg, LocalVolume(volume).listing())
    assert [p for batch in plan.batches for p, _ in batch] == ["runs/run1/events.jsonl"]
    migrate.files(cfg, LocalVolume(volume), keep_free_gb=0, say=quiet)
    assert (cfg.data_dir / "runs" / "run1" / "events.jsonl").read_bytes() == b"one\ntwo\n"


def test_a_file_the_native_app_wrote_is_never_overwritten(cfg, volume):
    migrate.files(cfg, LocalVolume(volume), keep_free_gb=0, say=quiet)
    write(cfg.data_dir / "runs" / "run1" / "final.png", b"written natively", mtime=time.time() + 1000)
    migrate.files(cfg, LocalVolume(volume), keep_free_gb=0, sample=0, say=quiet)
    assert (cfg.data_dir / "runs" / "run1" / "final.png").read_bytes() == b"written natively"


def test_different_keys_here_are_an_error_not_a_silent_loss_of_every_password(cfg, volume):
    """A data dir that started before the move made its own mail key; Docker's saved passwords would be unreadable."""
    write(cfg.data_dir / "mail-key", b"another-key", 0o600, mtime=time.time() + 1000)
    with pytest.raises(NativeError, match="mail-key"):
        migrate.files(cfg, LocalVolume(volume), keep_free_gb=0, sample=0, say=quiet)


def test_a_file_that_arrives_with_the_wrong_size_is_reported(cfg, volume, monkeypatch):
    plan = migrate.plan_files(cfg, LocalVolume(volume).listing())
    batch = [(p, s + 1) for p, s in plan.batches[0]]
    with pytest.raises(NativeError, match="wrong size"):
        migrate.fetch_batch(cfg, LocalVolume(volume), batch, cfg.migration / "incoming")


def test_every_command_against_docker_only_reads():
    source = Source("example-db-1", "example_applicant-data", "postgres:example")
    helper = source.helper_argv("find", "/data")
    assert "example_applicant-data:/data:ro" in helper, "the volume is mounted read-only"
    assert helper[:3] == ["docker", "run", "--rm"] and "--pull" in helper and "never" in helper
    assert "none" in helper and "--network" in helper
    assert source.dump_argv()[:4] == ["docker", "exec", "example-db-1", "pg_dump"]
    for sql in (migrate.TABLES, migrate.CLIENTS):
        assert source.psql_argv(sql)[3] == "psql" and sql.startswith("select")
    assert "-cf" in source.tar_argv() and "-xf" not in source.tar_argv()


def test_nothing_else_in_the_module_can_touch_docker():
    text = MODULE.read_text()
    outside = re.sub(r"class Source:.*?(?=\ndef source_from)", "", text, flags=re.S)
    assert '["docker"' not in outside, "Docker commands are built only in Source"
    for word in ("volume\", \"rm", "\"rm\"", "\"prune\"", "\"stop\"", "\"kill\"", "\"restart\""):
        assert word not in text, word


def test_counts_must_match_exactly():
    theirs = {"public.runs": 10, "public.jobs": 5, "dbos.workflow_status": 7}
    assert migrate.compare_counts(dict(theirs), theirs) == []
    differ = migrate.compare_counts({"public.runs": 9, "public.jobs": 5}, theirs)
    assert differ == ["dbos.workflow_status: Docker 7, here missing", "public.runs: Docker 10, here 9"]


def test_table_counts_are_read_in_one_statement():
    seen = []

    def query(sql):
        seen.append(sql)
        return "public.runs|3\ndbos.workflow_status|4"

    assert migrate.table_counts(query, ["public.runs", "dbos.workflow_status"]) == {
        "public.runs": 3,
        "dbos.workflow_status": 4,
    }
    assert len(seen) == 1 and 'from "dbos"."workflow_status"' in seen[0]


def fake_database(monkeypatch, tables_here="0", clients="0", size=1000):
    calls = []

    def psql(_bin, _cfg, sql, **_):
        calls.append(sql)
        if "pg_tables where schemaname in" in sql:
            return tables_here
        return "0"

    monkeypatch.setattr(pg, "psql", psql)
    monkeypatch.setattr(pg, "ensure_database", lambda *_: calls.append("ensure"))
    source = Source("example-db-1", "example_applicant-data", "postgres:example")
    monkeypatch.setattr(
        source, "query", lambda sql: clients if sql == migrate.CLIENTS else str(size) if "pg_database_size" in sql else ""
    )
    return source, calls


def test_the_dump_refuses_while_the_docker_app_is_connected(cfg, monkeypatch):
    """Anything it wrote after the dump would be lost."""
    source, _ = fake_database(monkeypatch, clients="28")
    with pytest.raises(NativeError, match="docker compose stop app"):
        migrate.database(cfg, source, Path("/nonexistent"), keep_free_gb=0, say=quiet)
    assert not (cfg.migration / "db-restore.started").exists()


def test_a_native_database_with_data_is_never_replaced_without_asking(cfg, monkeypatch):
    source, calls = fake_database(monkeypatch, tables_here="40")
    with pytest.raises(NativeError, match="--replace"):
        migrate.database(cfg, source, Path("/nonexistent"), keep_free_gb=0, say=quiet)
    assert not any("drop database" in c for c in calls)


def test_a_finished_restore_is_not_done_twice(cfg, monkeypatch, capsys):
    source, calls = fake_database(monkeypatch, tables_here="40")
    cfg.migration.mkdir(parents=True)
    (cfg.migration / "db-report.json").write_text(json.dumps({"ok": True}))
    assert migrate.database(cfg, source, Path("/nonexistent"), keep_free_gb=0) == 0
    assert "already restored" in capsys.readouterr().out
    assert not any("drop database" in c for c in calls)


def test_too_little_disk_refuses_before_anything_starts(cfg, monkeypatch):
    source, _ = fake_database(monkeypatch, size=10**15)
    with pytest.raises(NativeError, match="free"):
        migrate.database(cfg, source, Path("/nonexistent"), keep_free_gb=1, say=quiet)
    assert not (cfg.migration / "db-restore.started").exists()


def test_a_restore_that_fails_leaves_the_marker_that_blocks_start(cfg, monkeypatch):
    source, _ = fake_database(monkeypatch)
    monkeypatch.setattr(migrate, "free_bytes", lambda _: 100 * migrate.GIB)

    def broken(*_):
        raise NativeError("The restore failed")

    monkeypatch.setattr(migrate, "restore", broken)
    with pytest.raises(NativeError):
        migrate.database(cfg, source, Path("/nonexistent"), keep_free_gb=0, say=quiet)
    assert (cfg.migration / "db-restore.started").exists()


def test_only_queued_runs_are_repointed_at_the_native_data_dir(cfg, monkeypatch):
    seen = []
    monkeypatch.setattr(pg, "psql", lambda _b, _c, sql, **_: seen.append(sql) or "0")
    migrate.rewrite_queued_paths(Path("/nonexistent"), cfg)
    assert "where state = 'queued'" in seen[0] and f"'{cfg.data_dir}/'" in seen[0]


def test_the_hold_set_after_a_restore_is_valid_json(cfg, monkeypatch):
    seen = []
    monkeypatch.setattr(pg, "psql", lambda _b, _c, sql, **_: seen.append(sql))
    migrate.set_hold(Path("/nonexistent"), cfg, "draining", "it's held")
    payload = re.search(r"'(\{.*\})'::json", seen[0]).group(1).replace("''", "'")
    assert json.loads(payload)["stage"] == "draining"
    assert "on conflict (key) do update" in seen[0]


def test_dotenv_reads_what_setup_writes():
    assert read_dotenv(ROOT / "does-not-exist") == {}


def test_dotenv_values(tmp_path):
    env = tmp_path / ".env"
    env.write_text("# comment\nA=1\nexport B=two\nC=\"q z\"\nD='s'\nE=x # note\nF=\nnot a line\n")
    assert read_dotenv(env) == {"A": "1", "B": "two", "C": "q z", "D": "s", "E": "x", "F": ""}


def test_a_docker_env_file_works_unchanged(tmp_path):
    """`.env` stays the one source of config: Docker's address means this same computer."""
    (tmp_path / ".env").write_text(
        "POSTGRES_PASSWORD=a/b\nDATA_DIR=.data\nBROWSER_CDP_URL=http://host.docker.internal:9224\n"
        "BROWSER_TWIN_CDP_URL=http://host.docker.internal:9225\nDATABASE_URL=\n"
    )
    cfg = load(tmp_path, environ={})
    assert cfg.worker_cdp == "http://127.0.0.1:9224" and cfg.twin_cdp == "http://127.0.0.1:9225"
    assert cfg.data_dir == tmp_path / ".data"
    assert cfg.database_url.endswith(f"applypilot:a%2Fb@127.0.0.1:{cfg.pg_port}/applypilot")
    assert cfg.stop_grace == 180, "the same as compose.yaml's stop_grace_period"


def test_the_environment_outranks_the_file(tmp_path):
    (tmp_path / ".env").write_text("APP_PORT=8080\n")
    assert load(tmp_path, environ={"APP_PORT": "8180"}).app_port == 8180


def test_a_debugging_port_is_only_ever_local():
    assert local_cdp("") == ""
    assert local_cdp("http://localhost:9224/") == "http://localhost:9224"
    with pytest.raises(NativeError):
        local_cdp("http://192.0.2.10:9224")
    with pytest.raises(NativeError):
        local_cdp("https://127.0.0.1:9224")


def test_the_app_gets_what_compose_gave_it(tmp_path):
    cfg = load(tmp_path, environ={"POSTGRES_PASSWORD": "synthetic", "PATH": "/usr/bin", "TIMEZONE": "America/New_York"})
    env = cfg.app_environment(tmp_path / "release")
    assert env["DATABASE_URL"] == env["DBOS_DATABASE_URL"] == cfg.database_url
    assert env["DATA_DIR"] == str(cfg.data_dir)
    assert env["TZ"] == "UTC", "the container's clock: date.today() and now()::text rely on it"
    assert env["BASE_URL"] == "http://127.0.0.1:8080"


def test_postgres_is_local_small_and_durable():
    cfg = load(ROOT, environ={"POSTGRES_PASSWORD": "synthetic", "DATA_DIR": "/nonexistent"})
    argv = pg.server_argv(Path("/nonexistent"), cfg)
    assert "listen_addresses=127.0.0.1" in argv
    assert "wal_sync_method=fsync_writethrough" in argv, "macOS's plain fsync stops at the drive's cache"
    assert "synchronous_commit=off" in argv, "only Database.exclusive() pays for a flush on this event loop"
    assert "shared_buffers=64MB" in argv and "jit=off" in argv
    assert not any("synthetic" in str(arg) for arg in argv), "a password on a command line is visible in ps"


def test_max_connections_covers_the_real_pools():
    """A connection refused in the middle of a commit is an unknown submission."""
    from jobpilot.db import POOL_OVERFLOW, POOL_SIZE

    DBOS_SYSTEM_POOL = 20
    cfg = load(ROOT, environ={"POSTGRES_PASSWORD": "synthetic"})
    listed = services.read(cfg)
    assert sum(s.database_connections for s in listed) >= POOL_SIZE + POOL_OVERFLOW + DBOS_SYSTEM_POOL
    assert pg.max_connections(listed) >= POOL_SIZE + POOL_OVERFLOW + DBOS_SYSTEM_POOL + 5
    assert pg.max_connections(listed) == pg.MAX_CONNECTIONS, "the small machine's setting, while there is one service"


def test_a_cluster_of_another_major_version_is_not_used(cfg):
    cfg.pgdata.mkdir(parents=True)
    (cfg.pgdata / "PG_VERSION").write_text("15\n")
    with pytest.raises(NativeError, match="pg_upgrade"):
        pg.init_cluster(Path("/nonexistent"), cfg)


def test_a_folder_that_is_not_a_cluster_is_not_overwritten(cfg):
    cfg.pgdata.mkdir(parents=True)
    (cfg.pgdata / "keep").write_text("x")
    with pytest.raises(NativeError, match="Move it aside"):
        pg.init_cluster(Path("/nonexistent"), cfg)
    assert (cfg.pgdata / "keep").exists()


def test_a_real_cluster_is_private_and_uses_the_small_settings(cfg):
    """Needs a PostgreSQL 17 on this computer; CI has none."""
    try:
        bin_dir = pg.find_bin(cfg)
    except NativeError:
        pytest.skip("no PostgreSQL 17 here")
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    cfg = load(cfg.root, environ={"POSTGRES_PASSWORD": "synthetic", "DATA_DIR": str(cfg.data_dir), "AXIOM_PG_PORT": str(port)})
    assert pg.init_cluster(bin_dir, cfg) is True
    assert pg.init_cluster(bin_dir, cfg) is False, "an existing cluster is never replaced"
    assert cfg.pgdata.stat().st_mode & 0o077 == 0
    server = subprocess.Popen(
        pg.server_argv(bin_dir, cfg), env=pg.server_env(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    try:
        for _ in range(100):
            if pg.is_ready(bin_dir, cfg):
                break
            time.sleep(0.1)
        pg.ensure_database(bin_dir, cfg)
        pg.ensure_database(bin_dir, cfg)
        assert pg.psql(bin_dir, cfg, "show shared_buffers") == "64MB"
        assert pg.psql(bin_dir, cfg, "show timezone") == "UTC"
        assert pg.psql(bin_dir, cfg, f"select datlocprovider from pg_database where datname='{pg.DATABASE}'") in {"i", "c"}
        from sqlalchemy import text

        from jobpilot.db import Database

        database = Database(cfg.database_url)
        try:
            with database.session() as s:
                assert s.scalar(text("show synchronous_commit")) == "off"
            with database.exclusive() as s:
                assert s.scalar(text("show synchronous_commit")) == "on", "a reservation waits for the disk"
            with database.session() as s:
                assert s.scalar(text("show synchronous_commit")) == "off", "for its own transaction only"
        finally:
            database.engine.dispose()
        wrong = subprocess.run(
            [str(bin_dir / "psql"), *pg.connection(cfg), "-c", "select 1"],
            env={**os.environ, "PGPASSWORD": "wrong"},
            capture_output=True,
            text=True,
        )
        assert wrong.returncode != 0 and "password authentication failed" in wrong.stderr
    finally:
        server.send_signal(2)
        server.wait(timeout=30)
