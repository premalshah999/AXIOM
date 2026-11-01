"""Settings > Delete everything (2026-11-01): the data folder, database, Chrome profiles and database copies go; the installation can
start again as on a first run; and the Mac app's supervisor starts nothing while the eraser works."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from jobpilot import erase
from jobpilot.config import Settings
from jobpilot.native.supervisor import Child, Supervisor
from tests.test_native_database_choice import installation, no_postgres_binaries  # noqa: F401


def populated(tmp_path):
    data = tmp_path / "data"
    for name in ("runs/r1/steps", "resumes", "sessions", "desktop-browser/Default", "twin-browser", "archive", "migration", "native", "postgres"):
        (data / name).mkdir(parents=True)
    for name in ("application.db", "application.db-wal", "workflows.db", "mail-key", "connections.enc", "database.json", "database.enc",
                 "runs/r1/answers.json", "resumes/cv.pdf", "desktop-browser/Default/Cookies", "access-token", "native/state.json", "postgres/PG_VERSION"):
        (data / name).write_text("x")
    backups = tmp_path / "copies"
    backups.mkdir()
    (backups / "axiom-auto-20261101-000000.dump").write_text("d")
    (backups / "axiom-20261101.dump").write_text("d")
    (backups / "someone-elses.txt").write_text("keep")
    config = Settings(data_dir=data, database_url=f"sqlite:///{data}/application.db", dbos_database_url=f"sqlite:///{data}/workflows.db")
    return config, data, backups


def test_everything_goes_and_only_scaffolding_stays(tmp_path):
    config, data, backups = populated(tmp_path)
    assert erase.run(config, say=lambda _: None, backups=backups) == []
    assert sorted(p.name for p in data.iterdir()) == ["access-token", "native", "postgres"]
    assert (data / "postgres" / "PG_VERSION").exists() and (data / "native" / "state.json").exists()
    # The copies go, a stranger's file does not, and the folder stays because it is not empty.
    assert [p.name for p in backups.iterdir()] == ["someone-elses.txt"]


def test_an_empty_backup_folder_is_removed_with_its_last_copy(tmp_path):
    config, data, backups = populated(tmp_path)
    (backups / "someone-elses.txt").unlink()
    erase.run(config, say=lambda _: None, backups=backups)
    assert not backups.exists()


def test_the_eraser_command_refuses_without_a_yes(tmp_path):
    done = subprocess.run([sys.executable, "-m", "jobpilot.cli", "erase"], capture_output=True, text=True, cwd=tmp_path,
                          env={**os.environ, "PYTHONPATH": str(Path(erase.__file__).parents[1]), "DATA_DIR": str(tmp_path / "d"), "DATABASE_URL": f"sqlite:///{tmp_path}/d/a.db"})
    assert done.returncode != 0 and "no undo" in done.stderr
    assert "usage" not in done.stderr  # the command exists; it refused for the reason we gave


def test_a_marker_holds_only_while_its_eraser_lives(tmp_path):
    assert not erase.running(tmp_path)
    erase.marker(tmp_path).write_text(json.dumps({"pid": os.getpid()}))
    assert erase.running(tmp_path)
    erase.marker(tmp_path).write_text(json.dumps({"pid": 2**22 + 12345}))
    assert not erase.running(tmp_path)
    erase.marker(tmp_path).write_text("not json")
    assert not erase.running(tmp_path)


def test_the_endpoint_needs_the_exact_phrase(client):
    assert client.post("/api/system/erase", json={"confirm": "delete"}).status_code == 422
    assert client.post("/api/system/erase", json={}).status_code == 422
    assert client.get("/api/control").json()["paused"] is False  # a refused request changed nothing
    assert client.get("/api/system/erase").json()["cannot_remove"] == erase.CANNOT


def test_the_supervisor_starts_nothing_but_postgres_while_the_eraser_lives(tmp_path, no_postgres_binaries):  # noqa: F811
    cfg = installation(tmp_path)
    sup = Supervisor(cfg, say=lambda _: None)
    started = []

    def child(name):
        c = Child(name, ["true"])
        c.spawn = lambda: started.append(name)
        return c

    kids = [child("postgres"), child("app")]
    erase.marker(cfg.data_dir).write_text(json.dumps({"pid": os.getpid()}))
    for c in kids:
        sup.tick_child(c, 100.0)
    assert started == ["postgres"]
    erase.marker(cfg.data_dir).unlink()
    for c in kids:
        sup.tick_child(c, 100.0)
    assert started == ["postgres", "postgres", "app"]
