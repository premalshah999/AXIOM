"""The one-command bootstrap and the host Chrome launchers (no Docker, no real Chrome started)."""

import os
import re
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
BASE_ENV = {"PATH": os.environ["PATH"], "HOME": os.environ.get("HOME", "/tmp")}


@pytest.fixture
def checkout(tmp_path):
    """A minimal copy of the repo layout the scripts expect."""
    (tmp_path / "scripts" / "lib").mkdir(parents=True)
    for name in ("setup.sh", "chrome-worker.sh", "chrome-twin.sh", "lib/chrome.sh"):
        shutil.copy(ROOT / "scripts" / name, tmp_path / "scripts" / name)
    shutil.copy(ROOT / ".env.example", tmp_path / ".env.example")
    return tmp_path


def run(checkout, script, *args, **env):
    return subprocess.run(
        ["sh", str(checkout / "scripts" / script), *args],
        cwd=checkout,
        env={**BASE_ENV, **env},
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_first_run_creates_a_private_env_with_a_generated_password(checkout):
    result = run(checkout, "setup.sh", "--env-only", TIMEZONE="America/Los_Angeles")
    assert result.returncode == 0, result.stderr
    env = (checkout / ".env").read_text()
    password = re.search(r"^POSTGRES_PASSWORD=(.*)$", env, re.M).group(1)
    assert re.fullmatch(r"[0-9a-f]{48}", password), "a random 192-bit password, not a built-in default"
    assert "TIMEZONE=America/Los_Angeles" in env
    assert (checkout / ".env").stat().st_mode & 0o077 == 0, ".env must be readable by its owner only"


def test_custom_port_and_project_name_are_written_together(checkout):
    run(checkout, "setup.sh", "--env-only", APP_PORT="18080", COMPOSE_PROJECT_NAME="axiom-fresh")
    env = (checkout / ".env").read_text()
    # BASE_URL must follow the port or the API rejects every browser write.
    assert "APP_PORT=18080" in env and "BASE_URL=http://127.0.0.1:18080" in env
    assert "COMPOSE_PROJECT_NAME=axiom-fresh" in env


def test_rerun_never_replaces_the_existing_env_or_password(checkout):
    run(checkout, "setup.sh", "--env-only")
    before = (checkout / ".env").read_bytes()
    again = run(checkout, "setup.sh", "--env-only", APP_PORT="9999")
    assert again.returncode == 0
    assert (checkout / ".env").read_bytes() == before


def test_existing_env_without_a_password_is_refused_not_guessed(checkout):
    (checkout / ".env").write_text("LLM_PROVIDER=deepseek\n")
    result = run(checkout, "setup.sh", "--env-only")
    assert result.returncode != 0
    assert "POSTGRES_PASSWORD" in result.stderr and "local-container-database" in result.stderr
    assert (checkout / ".env").read_text() == "LLM_PROVIDER=deepseek\n"


def test_a_bad_port_is_rejected(checkout):
    result = run(checkout, "setup.sh", "--env-only", APP_PORT="80 80")
    assert result.returncode != 0 and "APP_PORT" in result.stderr
    assert not (checkout / ".env").exists()


def test_chrome_launchers_use_their_own_ports_and_profiles(checkout):
    worker = run(
        checkout, "chrome-worker.sh", DRY_RUN="1", CHROME_BIN="/bin/true", CHROME_WORKER_PORT="39224"
    )
    twin = run(checkout, "chrome-twin.sh", DRY_RUN="1", CHROME_BIN="/bin/true", CHROME_TWIN_PORT="39225")
    assert worker.returncode == 0 and twin.returncode == 0, worker.stderr + twin.stderr
    assert "--remote-debugging-port=39224" in worker.stdout.splitlines()
    assert "--remote-debugging-address=127.0.0.1" in worker.stdout.splitlines()
    assert any(line.endswith("/.data/desktop-browser") for line in worker.stdout.splitlines())
    assert "--no-startup-window" in twin.stdout.splitlines()
    assert any(line.endswith("/.data/twin-browser") for line in twin.stdout.splitlines())
    assert "--no-startup-window" not in worker.stdout


def test_a_running_chrome_is_left_alone(checkout):
    class Version(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"Browser": "Chrome"}')

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Version)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        result = run(
            checkout,
            "chrome-worker.sh",
            DRY_RUN="1",
            CHROME_BIN="/bin/true",
            CHROME_WORKER_PORT=str(server.server_port),
        )
    finally:
        server.shutdown()
    assert result.returncode == 0
    assert "already running" in result.stdout
    assert "--user-data-dir" not in result.stdout
