"""The native app starts PostgreSQL only when Local is chosen, follows a change of choice, and talks to a remote database safely (2026-10-31)."""

import json
import subprocess
from pathlib import Path

import pytest

from jobpilot.native import cli, pg
from jobpilot.native.env import NativeError, load, prepare
from jobpilot.native.supervisor import Supervisor
from jobpilot.storage import choice, mode

SUPABASE = "postgresql://postgres.abc:s3cret-pass@aws-0-us-east-1.pooler.supabase.com:5432/postgres"


def installation(tmp_path, *lines, password="synthetic"):
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / ".env").write_text("\n".join([f"DATA_DIR={tmp_path}/data", "APP_PORT=59190", *([f"POSTGRES_PASSWORD={password}"] if password else []), *lines]))
    return prepare(load(tmp_path, environ={}))


@pytest.fixture
def no_postgres_binaries(monkeypatch):
    monkeypatch.setattr(pg, "find_bin", lambda cfg: Path("/nonexistent"))
    monkeypatch.setattr(pg, "init_cluster", lambda *_: False)


def names(sup):
    return [c.name for c in sup.plan()]


def test_local_starts_postgresql_and_the_app_waits_for_it(tmp_path, no_postgres_binaries):
    sup = Supervisor(installation(tmp_path), say=lambda _: None)
    assert names(sup) == ["postgres", "app"] and sup.database == ("local", 0)


def test_a_remote_choice_starts_no_postgresql_at_all(tmp_path, no_postgres_binaries):
    cfg = installation(tmp_path, "DATABASE_MODE=supabase")
    sup = Supervisor(cfg, say=lambda _: None)
    children = sup.plan()
    assert [c.name for c in children] == ["app"] and children[0].after == ()
    assert sup.database[0] == "supabase"


def test_a_saved_choice_is_read_from_the_data_folder_too(tmp_path, no_postgres_binaries):
    cfg = installation(tmp_path)
    mode.write(cfg.data_dir, "server")
    assert names(Supervisor(cfg, say=lambda _: None)) == ["app"]


def test_a_remote_installation_needs_no_local_password_and_no_postgresql_install(tmp_path, monkeypatch):
    cfg = installation(tmp_path, "DATABASE_MODE=server", password="")
    assert cfg.password == ""
    monkeypatch.setattr(pg, "find_bin", lambda cfg: (_ for _ in ()).throw(NativeError("not installed")))
    sup = Supervisor(cfg, say=lambda _: None)
    assert [c.name for c in sup.plan()] == ["app"] and sup.pg_bin is None
    with pytest.raises(NativeError, match="POSTGRES_PASSWORD"):
        installation(tmp_path / "other", password="")


def test_a_change_between_local_and_remote_restarts_the_supervisor_on_a_new_plan(tmp_path, no_postgres_binaries):
    cfg = installation(tmp_path)
    sup = Supervisor(cfg, say=lambda _: None)
    sup.children = sup.plan()
    sup.tick_database()
    assert not sup.reexec
    mode.write(cfg.data_dir, "supabase")
    sup.tick_database()
    assert sup.reexec and sup.stop_reason == "The database choice changed: restarting on it."


def test_a_new_string_for_the_same_kind_of_database_restarts_only_the_services(tmp_path, no_postgres_binaries):
    cfg = installation(tmp_path)
    mode.write(cfg.data_dir, "supabase")
    sup = Supervisor(cfg, say=lambda _: None)
    sup.children = sup.plan()
    mode.write(cfg.data_dir, "supabase")  # Saved again with another string: the revision rises, the kind stays.
    sup.tick_database()
    assert sup.restart_requested and not sup.reexec and not sup.stop_reason


def test_psql_reaches_a_remote_database_through_the_environment_not_the_command_line(tmp_path, monkeypatch):
    cfg = installation(tmp_path)
    choice.save(type("C", (), {"data_dir": cfg.data_dir, "mail_encryption_key": ""})(), "supabase", url=SUPABASE)
    cfg = load(tmp_path, environ={})
    seen = {}

    def run(argv, **kwargs):
        seen.update(argv=argv, env=kwargs["env"])
        return subprocess.CompletedProcess(argv, 0, stdout="3\n", stderr="")

    monkeypatch.setattr(subprocess, "run", run)
    assert pg.psql(Path("/bin"), cfg, "select count(*) from runs") == "3"
    assert "s3cret-pass" not in " ".join(map(str, seen["argv"])) and "-h" not in seen["argv"]
    assert seen["env"]["PGPASSWORD"] == "s3cret-pass" and seen["env"]["PGOPTIONS"] == "-c search_path=axiom"
    assert seen["env"]["PGHOST"].endswith("supabase.com")


def test_local_psql_is_unchanged(tmp_path, monkeypatch):
    cfg = installation(tmp_path)
    seen = {}
    monkeypatch.setattr(subprocess, "run", lambda argv, **kw: seen.update(argv=argv, env=kw["env"]) or subprocess.CompletedProcess(argv, 0, stdout="1", stderr=""))
    pg.psql(Path("/bin"), cfg, "select 1")
    assert "-h" in seen["argv"] and "127.0.0.1" in seen["argv"] and seen["env"]["PGPASSWORD"] == "synthetic" and "PGOPTIONS" not in seen["env"]


def test_a_docker_installation_is_only_ever_restored_into_the_local_database(tmp_path):
    cfg = installation(tmp_path, "DATABASE_MODE=server")
    args = cli.parser().parse_args(["migrate", "plan"])
    with pytest.raises(NativeError, match="DATABASE_MODE=local"):
        cli.cmd_migrate(cfg, args)


def test_the_status_file_says_which_database_is_in_use(tmp_path, no_postgres_binaries):
    cfg = installation(tmp_path, "DATABASE_MODE=server")
    sup = Supervisor(cfg, say=lambda _: None)
    sup.children = sup.plan()
    sup.write_state(force=True)
    assert json.loads(cfg.state_file.read_text())["database"] == "server"


def fake_tools(monkeypatch, dumped=0, listed=0, toc=";\n1; 2 3 TABLE DATA public jobs owner\n"):
    calls = []

    def run(argv, **kw):
        calls.append((argv, kw.get("env") or {}))
        if "pg_dump" in argv[0]:
            kw["stdout"].write(b"PGDMP")
            return subprocess.CompletedProcess(argv, dumped, b"", b"dump failed")
        return subprocess.CompletedProcess(argv, listed, toc, "")

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(pg, "find_bin", lambda cfg: Path("/pg/bin"))
    return calls


def test_a_backup_is_private_read_back_and_never_shows_the_password(tmp_path, monkeypatch, capsys):
    cfg = installation(tmp_path)
    calls = fake_tools(monkeypatch)
    target = tmp_path / "out" / "copy.dump"
    assert cli.cmd_backup(cfg, cli.parser().parse_args(["backup", str(target)])) == 0
    assert target.read_bytes() == b"PGDMP" and target.stat().st_mode & 0o077 == 0
    assert [Path(argv[0]).name for argv, _ in calls] == ["pg_dump", "pg_restore"]
    assert all("synthetic" not in " ".join(argv) for argv, _ in calls) and calls[0][1]["PGPASSWORD"] == "synthetic"
    assert "synthetic" not in capsys.readouterr().out
    assert not list((tmp_path / "out").glob("*.partial"))


@pytest.mark.parametrize("failure", [{"dumped": 1}, {"listed": 1}, {"toc": ""}])
def test_a_failed_backup_leaves_no_file_that_looks_good(tmp_path, monkeypatch, failure):
    cfg = installation(tmp_path)
    fake_tools(monkeypatch, **failure)
    target = tmp_path / "out" / "copy.dump"
    with pytest.raises(NativeError):
        cli.cmd_backup(cfg, cli.parser().parse_args(["backup", str(target)]))
    assert not target.exists() and not list((tmp_path / "out").glob("*"))
