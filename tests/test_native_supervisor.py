"""The native supervisor, driven with small shell children: what Compose and Docker's init did for the containers."""

import os
import signal
import socket
import subprocess
import sys
import time
import tomllib
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Thread

import pytest

from jobpilot.native import pg, release, services, supervisor
from jobpilot.native.env import NativeError, load, prepare
from jobpilot.native.supervisor import Child, Supervisor

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def cfg(tmp_path):
    (tmp_path / ".env").write_text(f"POSTGRES_PASSWORD=synthetic\nDATA_DIR={tmp_path}/data\nAPP_PORT=59180\n")
    return prepare(load(tmp_path, environ={}))


def child(name, script, **kwargs):
    kwargs.setdefault("ready", lambda: True)
    return Child(name, ["sh", "-c", script], **kwargs)


def advance(sup, until, seconds=6.0):
    """Tick the supervisor until `until()` holds, as its loop does."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        sup.tick(time.monotonic())
        if until():
            return True
        time.sleep(0.05)
    return False


def supervised(cfg, *children):
    sup = Supervisor(cfg, list(children), say=lambda line: sup.lines.append(line))
    sup.lines = []
    sup.next_tick = {"sleep": 1e18, "power": 1e18, "rotate": 1e18}
    return sup


def alive(pid):
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def test_a_child_that_crashes_is_started_again(cfg):
    worker = child("worker", "sleep 30", url="http://127.0.0.1:1")
    sup = supervised(cfg, worker)
    assert advance(sup, lambda: worker.state == "up")
    first = worker.pid
    os.kill(first, signal.SIGKILL)
    assert advance(sup, lambda: worker.pid not in (None, first) and worker.state == "up")
    assert worker.restarts == 1
    sup.shutdown()


def test_a_child_that_keeps_dying_stops_everything_with_a_clear_message(cfg, monkeypatch):
    monkeypatch.setattr(supervisor, "MAX_STRIKES", 2)
    app = child("app", "exit 3", log=cfg.logs / "app.log")
    sup = supervised(cfg, app)
    assert advance(sup, lambda: sup.stop_reason, seconds=25)
    assert "app is not running" in sup.stop_reason and "status 3" in sup.stop_reason
    assert str(cfg.logs / "app.log") in sup.stop_reason
    sup.shutdown()
    state = supervisor.read_state(cfg)
    assert state["phase"] == "stopped" and "app is not running" in state["reason"]


def test_an_optional_child_that_keeps_dying_does_not_stop_the_rest(cfg, monkeypatch):
    monkeypatch.setattr(supervisor, "MAX_STRIKES", 2)
    twin = child("twin", "exit 1", required=False, url="http://127.0.0.1:1")
    other = child("app", "sleep 30")
    sup = supervised(cfg, twin, other)
    assert advance(sup, lambda: twin.state == "failed", seconds=25)
    assert not sup.stop_reason and other.state == "up"
    sup.shutdown()


def test_exit_zero_after_a_long_run_is_a_restart_not_a_crash(cfg, monkeypatch):
    """POST /api/system/restart exits 0 and relied on Docker's restart policy."""
    monkeypatch.setattr(supervisor, "STABLE_SECONDS", 0.2)
    app = child("app", "sleep 0.4")
    sup = supervised(cfg, app)
    assert advance(sup, lambda: app.restarts >= 2)
    assert app.strikes == 0 and not sup.stop_reason
    sup.shutdown()


def test_leftovers_of_a_dead_child_are_killed_with_its_group(cfg, tmp_path):
    """Docker's init and the container's end did this for an orphaned Playwright driver or headless Chromium."""
    marker = tmp_path / "grandchild.pid"
    app = child("app", f"sleep 60 & echo $! > {marker}; wait")
    sup = supervised(cfg, app)
    assert advance(sup, lambda: marker.exists() and marker.read_text().strip())
    grandchild = int(marker.read_text())
    assert alive(grandchild)
    os.kill(app.pid, signal.SIGKILL)
    assert advance(sup, lambda: app.restarts >= 1)
    for _ in range(50):
        if not alive(grandchild):
            break
        time.sleep(0.1)
    assert not alive(grandchild)
    sup.shutdown()


def test_the_app_drains_with_its_browser_driver_alive(cfg, tmp_path):
    """SIGTERM goes to the app alone: sent to the group, it would kill Playwright's driver before commits finish."""
    trace = tmp_path / "trace"
    script = f"""
      sleep 60 & kid=$!
      trap 'if kill -0 $kid 2>/dev/null; then echo driver-alive >> {trace}; fi; kill $kid; exit 0' TERM
      echo ready >> {trace}
      wait
    """
    app = child("app", script, grace=5.0)
    sup = supervised(cfg, app)
    assert advance(sup, lambda: trace.exists())
    time.sleep(0.2)
    sup.stop_reason = "Stopped by request."
    sup.shutdown()
    assert "driver-alive" in trace.read_text()


def test_a_stop_drains_the_app_first_and_stops_the_database_last(cfg, tmp_path):
    order = tmp_path / "order"
    def named(name, signal_name):
        return f"trap 'echo {name} >> {order}; exit 0' {signal_name}; while :; do sleep 0.1; done"

    database = child("postgres", named("postgres", "INT"), stop_signal=signal.SIGINT, grace=5.0)
    worker = child("worker", named("worker", "TERM"), url="http://127.0.0.1:1", grace=5.0)
    app = child("app", named("app", "TERM"), after=(database, worker), grace=5.0)
    pg_ensure, pg.ensure_database = pg.ensure_database, lambda *_: None
    try:
        sup = supervised(cfg, database, worker, app)
        assert advance(sup, lambda: app.state == "up")
        time.sleep(0.3)  # Let each shell install its trap.
        sup.stop_reason = "Stopped by request."
        sup.shutdown()
    finally:
        pg.ensure_database = pg_ensure
    assert order.read_text().split() == ["app", "worker", "postgres"]


def test_an_app_that_ignores_a_stop_is_killed_only_after_the_whole_grace(cfg):
    app = child("app", "trap '' TERM; while :; do sleep 0.1; done", grace=1.0)
    sup = supervised(cfg, app)
    assert advance(sup, lambda: app.state == "up")
    time.sleep(0.2)
    began = time.monotonic()
    pid = app.pid
    app.final_signal = signal.SIGKILL
    sup.stop_reason = "Stopped by request."
    sup.shutdown()
    assert time.monotonic() - began >= 1.0 and not alive(pid)


def test_the_app_waits_for_the_database_and_restarts_after_it_comes_back(cfg, monkeypatch):
    """In-flight runs stay `running` until the app restarts, and its drain needs the database (docs/INFRA.md)."""
    monkeypatch.setattr(pg, "ensure_database", lambda *_: None)
    ready = {"postgres": False}
    database = child("postgres", "sleep 60", ready=lambda: ready["postgres"], stop_signal=signal.SIGINT)
    app = child("app", "sleep 60", after=(database,), restart_with=("postgres",))
    sup = supervised(cfg, database, app)
    assert advance(sup, lambda: database.state == "starting")
    for _ in range(10):
        sup.tick(time.monotonic())
    assert app.proc is None and app.state == "down", "the app must wait for the database"
    ready["postgres"] = True
    assert advance(sup, lambda: app.state == "up")
    first = app.pid
    os.kill(database.pid, signal.SIGKILL)
    assert advance(sup, lambda: database.restarts == 1 and database.state == "up", seconds=8)
    assert advance(sup, lambda: app.pid not in (None, first) and app.state == "up", seconds=8)
    sup.shutdown()


def test_a_chrome_someone_else_started_is_left_alone_and_replaced_when_it_goes(cfg):
    class Version(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Version)
    Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_port}"
    calls = []

    def argv():
        calls.append(1)
        return None if supervisor.http_ok(url + "/json/version") else ["sh", "-c", "sleep 30"]

    worker = Child("worker", argv, ready=lambda: True, url=url)
    sup = supervised(cfg, worker)
    assert advance(sup, lambda: worker.state == "external")
    assert worker.proc is None
    sup.shutdown()  # A stop must not touch it.
    assert supervisor.http_ok(url + "/json/version")
    server.shutdown()
    server.server_close()
    sup = supervised(cfg, worker)
    assert advance(sup, lambda: worker.state == "up")
    sup.shutdown()


def test_chrome_flags_come_from_the_scripts_and_stay_on_the_requested_port(cfg, monkeypatch):
    monkeypatch.setenv("CHROME_BIN", "/bin/true")
    cfg = load(ROOT, environ={"DATA_DIR": str(cfg.data_dir), "POSTGRES_PASSWORD": "synthetic"})
    argv = supervisor.chrome_argv(
        cfg,
        "chrome-worker.sh",
        "CHROME_WORKER_PORT",
        "CHROME_WORKER_PROFILE",
        "http://127.0.0.1:39224",
        cfg.data_dir / "desktop-browser",
        "--headless=new",
    )
    assert argv[0] == "/bin/true" and "--headless=new" in argv
    assert "--remote-debugging-port=39224" in argv and "--remote-debugging-address=127.0.0.1" in argv
    assert f"--user-data-dir={cfg.data_dir}/desktop-browser" in argv
    assert (cfg.data_dir / "desktop-browser").stat().st_mode & 0o077 == 0


def test_chrome_that_already_answers_is_not_started_a_second_time(cfg, monkeypatch):
    class Version(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Version)
    Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setenv("CHROME_BIN", "/bin/true")
    cfg = load(ROOT, environ={"DATA_DIR": str(cfg.data_dir), "POSTGRES_PASSWORD": "synthetic"})
    try:
        argv = supervisor.chrome_argv(
            cfg,
            "chrome-worker.sh",
            "CHROME_WORKER_PORT",
            "CHROME_WORKER_PROFILE",
            f"http://127.0.0.1:{server.server_port}",
            cfg.data_dir / "desktop-browser",
        )
    finally:
        server.shutdown()
        server.server_close()
    assert argv is None


def test_sleep_is_prevented_only_while_applications_run_and_released_after_a_quiet_spell(cfg, monkeypatch):
    started = []

    class Fake:
        def __init__(self, argv, **_):
            started.append(argv)
            self.running = True

        def poll(self):
            return None if self.running else 0

        def terminate(self):
            self.running = False

        def wait(self):
            return 0

    sup = supervised(cfg, child("postgres", "sleep 1"))
    sup.children[0].state = "up"
    monkeypatch.setattr(supervisor.subprocess, "Popen", Fake)
    monkeypatch.setattr(supervisor.shutil, "which", lambda name: "/usr/bin/" + name)
    active = {"count": "0"}
    monkeypatch.setattr(pg, "psql", lambda *a, **k: active["count"])
    sup.tick_sleep(100.0)
    assert not started, "nothing active: the Mac may sleep"
    active["count"] = "3"
    sup.tick_sleep(120.0)
    assert started and started[0][:3] == ["caffeinate", "-i", "-m"]
    assert started[0][-2:] == ["-w", str(os.getpid())], "it must die with the supervisor"
    active["count"] = "0"
    sup.tick_sleep(120.0 + supervisor.SLEEP_RELEASE_SECONDS - 1)
    assert sup.caffeinate is not None, "a gap between applications must not let the Mac sleep"
    sup.tick_sleep(120.0 + supervisor.SLEEP_RELEASE_SECONDS + 1)
    assert sup.caffeinate is None
    active["count"] = None
    sup.tick_sleep(1000.0)
    assert sup.caffeinate is None, "unknown keeps what we had"


def test_battery_is_read_from_pmset(monkeypatch):
    out = "Now drawing from 'Battery Power'\n -InternalBattery-0 (id=1)\t32%; discharging; 0:50 remaining present: true\n"
    monkeypatch.setattr(supervisor.shutil, "which", lambda name: "/usr/bin/pmset")
    monkeypatch.setattr(
        supervisor.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout=out, stderr="")
    )
    assert supervisor.power_status() == {"battery": True, "percent": 32, "remaining": "0:50"}


def test_the_status_warns_on_battery(cfg, monkeypatch, capsys):
    from jobpilot.native import cli

    monkeypatch.setattr(supervisor, "power_status", lambda: {"battery": True, "percent": 32, "remaining": "0:50"})
    args = type("A", (), {"running": False, "json": False})()
    code = cli.cmd_status(cfg, args)
    out = capsys.readouterr().out
    assert code == 1 and "stopped" in out and "On battery (32%" in out


def test_a_second_instance_beside_docker_is_refused(cfg, monkeypatch):
    """Two databases applying to the same jobs would send every application twice."""
    monkeypatch.setattr(supervisor, "docker_instances", lambda: ["applypilot-app-1"])
    sup = supervised(cfg)
    with pytest.raises(NativeError, match="also running in Docker"):
        sup.preflight()


def test_beside_docker_it_never_shares_a_chrome(tmp_path, monkeypatch):
    class Version(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Version)
    Thread(target=server.serve_forever, daemon=True).start()
    env = {
        "POSTGRES_PASSWORD": "synthetic",
        "DATA_DIR": str(tmp_path / "data"),
        "AXIOM_ALLOW_DOCKER": "1",
        "APP_PORT": "59181",
        "AXIOM_PG_PORT": "59182",
        "BROWSER_CDP_URL": f"http://127.0.0.1:{server.server_port}",
    }
    cfg = prepare(load(tmp_path, environ=env))
    monkeypatch.setattr(supervisor, "docker_instances", lambda: ["applypilot-app-1"])
    sup = supervised(cfg)
    try:
        with pytest.raises(NativeError, match="not ours to drive"):
            sup.preflight()
    finally:
        server.shutdown()
        server.server_close()


def test_a_taken_port_is_named(cfg, monkeypatch):
    monkeypatch.setattr(supervisor, "docker_instances", lambda: [])
    with socket.socket() as s:
        s.bind(("127.0.0.1", cfg.app_port))
        s.listen()
        with pytest.raises(NativeError, match=f"Port {cfg.app_port}"):
            supervised(cfg).preflight()


def test_a_half_restored_database_is_never_started(cfg, monkeypatch):
    monkeypatch.setattr(supervisor, "docker_instances", lambda: [])
    cfg.migration.mkdir(parents=True)
    (cfg.migration / "db-restore.started").write_text("1")
    with pytest.raises(NativeError, match="did not finish"):
        supervised(cfg).preflight()


def test_a_killed_supervisor_leaves_nothing_that_stop_cannot_find(cfg):
    process = subprocess.Popen(["sleep", "60"], start_new_session=True)
    state = {
        "children": {"app": {"pid": process.pid, "lstart": supervisor.process_start(process.pid), "state": "up"}},
    }
    reaper = Thread(target=process.wait, daemon=True)
    reaper.start()
    assert supervisor.stop_leftovers(cfg, state, say=lambda _: None)
    reaper.join(timeout=5)
    assert process.poll() is not None


def test_a_reused_pid_is_not_mistaken_for_our_child(cfg):
    process = subprocess.Popen(["sleep", "60"], start_new_session=True)
    try:
        state = {"children": {"app": {"pid": process.pid, "lstart": "Thu Jan  1 00:00:00 1970", "state": "up"}}}
        assert not supervisor.stop_leftovers(cfg, state, say=lambda _: None)
        assert process.poll() is None
    finally:
        process.kill()
        process.wait()


def test_a_stale_postgres_lock_file_never_signals_its_pid(cfg):
    """postmaster.pid survives a crash, and by then its pid may belong to anything."""
    process = subprocess.Popen(["sleep", "60"], start_new_session=True)
    try:
        cfg.pgdata.mkdir(parents=True)
        (cfg.pgdata / "postmaster.pid").write_text(f"{process.pid}\n{cfg.pgdata}\n")
        assert not supervisor.stop_stray_postgres(cfg, lambda _: None)
        assert process.poll() is None
    finally:
        process.kill()
        process.wait()


def test_logs_are_capped_as_under_compose(cfg, monkeypatch):
    monkeypatch.setattr(supervisor, "LOG_BYTES", 100)
    path = cfg.logs / "app.log"
    with open(path, "ab") as held:  # A child's O_APPEND descriptor stays valid across rotation.
        held.write(b"x" * 200)
        held.flush()
        sup = supervised(cfg, child("app", "true", log=path))
        sup.rotate_logs()
        assert path.stat().st_size == 0
        assert (cfg.logs / "app.log.1").stat().st_size == 200
        held.write(b"y")
        held.flush()
    assert path.read_bytes() == b"y"


def test_the_file_limit_is_raised_for_every_child(cfg):
    """macOS starts a process with 256 descriptors. Docker's default is a million."""
    code = (
        "import resource\nfrom jobpilot.native.supervisor import Supervisor\n"
        "resource.setrlimit(resource.RLIMIT_NOFILE, (256, resource.getrlimit(resource.RLIMIT_NOFILE)[1]))\n"
        "Supervisor.raise_file_limit(None)\nprint(resource.getrlimit(resource.RLIMIT_NOFILE)[0])\n"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=ROOT)
    assert out.returncode == 0, out.stderr
    assert int(out.stdout) >= 1024


def test_the_release_is_a_snapshot_that_a_merge_cannot_reach(tmp_path):
    """Most imports here are inside functions, so a process running from the checkout would load tomorrow's merge."""
    root = tmp_path / "checkout"
    (root / "jobpilot" / "static").mkdir(parents=True)
    (root / "jobpilot" / "static" / "index.html").write_text("<html>")
    (root / "jobpilot" / "module.py").write_text("VERSION = 1\n")
    (root / "jobpilot" / "__pycache__").mkdir()
    (root / "jobpilot" / "__pycache__" / "module.pyc").write_text("x")
    (root / "uv.lock").write_text("lock")
    cfg = prepare(load(root, environ={"POSTGRES_PASSWORD": "synthetic", "DATA_DIR": str(tmp_path / "data")}))
    first = release.ensure(cfg)
    assert (first / "jobpilot" / "module.py").read_text() == "VERSION = 1\n"
    assert not (first / "jobpilot" / "__pycache__").exists()
    (root / "jobpilot" / "module.py").write_text("VERSION = 2\n")
    assert release.ensure(cfg) == first, "starting again keeps the deployed release"
    assert (first / "jobpilot" / "module.py").read_text() == "VERSION = 1\n"
    second = release.make(cfg)
    assert release.current(cfg) == first, "a new release is not in use until it is activated"
    release.activate(cfg, second)
    assert release.current(cfg) == second.resolve()
    assert (second / "jobpilot" / "module.py").read_text() == "VERSION = 2\n"
    assert first.exists(), "the previous release stays for rollback"
    env = cfg.app_environment(release.current(cfg))
    assert env["PYTHONPATH"] == str(second.resolve()) and env["PYTHONSAFEPATH"] == "1"


def test_old_releases_are_pruned(tmp_path):
    root = tmp_path / "checkout"
    (root / "jobpilot" / "static").mkdir(parents=True)
    (root / "jobpilot" / "static" / "index.html").write_text("<html>")
    cfg = prepare(load(root, environ={"POSTGRES_PASSWORD": "synthetic", "DATA_DIR": str(tmp_path / "data")}))
    names = []
    for n in range(5):
        target = cfg.releases / f"2026100{n}-000000-abc"
        target.mkdir()
        names.append(target)
        release.activate(cfg, target)
    assert sorted(p.name for p in cfg.releases.iterdir()) == [p.name for p in names[-3:]]


def test_deploy_falls_through_to_docker_when_native_is_not_running(tmp_path):
    done = subprocess.run(
        ["sh", str(ROOT / "scripts" / "axiom"), "status", "--running"],
        env={**os.environ, "AXIOM_VENV": str(tmp_path / "none"), "UV_PROJECT_ENVIRONMENT": ""},
        capture_output=True,
        text=True,
    )
    assert done.returncode == 1, "a status must never install anything"
    assert not (tmp_path / "none").exists()
    assert 'exec scripts/axiom deploy "$@"' in (ROOT / "scripts" / "deploy.sh").read_text()


# ---- the list of services is data ------------------------------------------------------------------------------------


def write_list(tmp_path, text):
    path = tmp_path / "services.toml"
    path.write_text(text)
    return path


def listed(tmp_path, text, **env):
    path = write_list(tmp_path, text)
    return load(tmp_path, environ={"POSTGRES_PASSWORD": "synthetic", "AXIOM_SERVICES": str(path), **env})


ONE = '''
[[service]]
name = "gateway"
command = ["{python}", "-m", "gateway", "--port", "{port}", "--data", "{data_dir}"]
port = 8090
health_url = "http://127.0.0.1:{port}/healthz"
'''


def test_the_shipped_list_is_todays_one_app():
    cfg = load(ROOT, environ={"POSTGRES_PASSWORD": "synthetic", "APP_PORT": "8180"})
    found = services.parse(tomllib.loads(services.PACKAGED.read_text()), cfg)
    assert [s.name for s in found] == ["app"], "the split comes later, as entries in this file"
    app = found[0]
    assert app.command == (sys.executable, "-m", "jobpilot.cli", "serve", "--host", "127.0.0.1", "--port", "8180")
    assert app.health_url == "http://127.0.0.1:8180/healthz"
    assert app.port == 8180 and app.restart == "always" and app.required and app.drains
    assert app.after == ("postgres", "worker", "twin") and app.restart_with == ("postgres",)


def test_a_new_service_is_only_an_entry(tmp_path):
    found = services.read(listed(tmp_path, ONE))
    assert found[0].name == "gateway" and found[0].port == 8090
    assert found[0].command[0] == sys.executable and found[0].command[3:] == ("--port", "8090", "--data", str(tmp_path / ".data"))
    assert found[0].restart == "always" and found[0].required and not found[0].drains


@pytest.mark.parametrize(
    "text, message",
    [
        ("", "no \\[\\[service\\]\\] entries"),
        ('[[service]]\nname = "App"\ncommand = ["x"]', "lowercase name"),
        ('[[service]]\nname = "postgres"\ncommand = ["x"]', "reserved"),
        ('[[service]]\nname = "a"', "command must be a list"),
        ('[[service]]\nname = "a"\ncommand = ["x"]\nrestart = "sometimes"', "restart must be one of"),
        ('[[service]]\nname = "a"\ncommand = ["x"]\nrestart_policy = "always"', "unknown field"),
        ('[[service]]\nname = "a"\ncommand = ["x", "{nope}"]', "uses {nope}"),
        ('[[service]]\nname = "a"\ncommand = ["x", "{port}"]', "uses {port}"),
        ('[[service]]\nname = "a"\ncommand = ["x", "--host", "0.0.0.0"]', "127.0.0.1 only"),
        ('[[service]]\nname = "a"\ncommand = ["x"]\nhealth_url = "http://example.test/healthz"', "must be local"),
        ('[[service]]\nname = "a"\ncommand = ["x"]\nport = 70000', "port must be"),
        ('[[service]]\nname = "a"\ncommand = ["x"]\nafter = ["b"]', "nothing starts"),
        ('[[service]]\nname = "a"\ncommand = ["x"]\nrestart_with = ["b"]', "nothing starts"),
        ('[[service]]\nname = "a"\ncommand = ["x"]\n[[service]]\nname = "a"\ncommand = ["y"]', "twice"),
        (
            '[[service]]\nname = "a"\ncommand = ["x"]\nafter = ["b"]\n[[service]]\nname = "b"\ncommand = ["y"]\nafter = ["a"]',
            "circle",
        ),
        ('[[service]]\nname = "a"\ncommand = ["x"]\nport = 8090\n[[service]]\nname = "b"\ncommand = ["y"]\nport = 8090', "already"),
        ('[[service]]\nname = "a"\ncommand = ["x"]\nport = 54329', "the database"),
        ("not toml =", "not valid TOML"),
    ],
)
def test_a_mistake_in_the_list_is_named_before_anything_starts(tmp_path, text, message):
    with pytest.raises(NativeError, match=message):
        services.read(listed(tmp_path, text))


def test_the_port_setting_comes_from_env(tmp_path):
    text = ONE + 'port_setting = "GATEWAY_PORT"\n'
    assert services.read(listed(tmp_path, text, GATEWAY_PORT="8091"))[0].port == 8091
    with pytest.raises(NativeError, match="GATEWAY_PORT must be a number"):
        services.read(listed(tmp_path, text, GATEWAY_PORT="eighty"))


def test_a_release_keeps_the_list_it_was_made_with(tmp_path, monkeypatch):
    """A merge that adds a service must not make an old release try to start code it does not contain."""
    root = tmp_path / "checkout"
    (root / "jobpilot" / "static").mkdir(parents=True)
    (root / "jobpilot" / "static" / "index.html").write_text("<html>")
    (root / "jobpilot" / "native").mkdir()
    (root / "jobpilot" / "native" / "services.toml").write_text(ONE)
    cfg = prepare(load(root, environ={"POSTGRES_PASSWORD": "synthetic", "DATA_DIR": str(tmp_path / "data")}))
    first = release.ensure(cfg)
    (root / "jobpilot" / "native" / "services.toml").write_text(ONE.replace("gateway", "other"))
    assert services.read(cfg, first)[0].name == "gateway"


def several(cfg, tmp_path, order=None):
    """Three listed services, as the supervisor builds them from the list."""
    marks = {}

    def run(name):
        marks[name] = tmp_path / name
        trap = f"trap 'echo {name} >> {order}; exit 0' TERM; " if order else ""
        return child(name, f"{trap}touch {marks[name]}; while :; do sleep 0.1; done", service=True, grace=3.0)

    database = child("postgres", "while :; do sleep 0.1; done", stop_signal=signal.SIGINT, grace=3.0)
    applier = run("applier")
    applier.after = (database,)
    mail = run("mail")
    mail.after = (database,)
    gateway = run("gateway")
    gateway.after = (applier, mail)
    return database, applier, mail, gateway


def test_several_services_start_in_order_and_the_whole_is_up_only_when_all_are(cfg, tmp_path, monkeypatch):
    monkeypatch.setattr(pg, "ensure_database", lambda *_: None)
    database, applier, mail, gateway = several(cfg, tmp_path)
    open_mail = {"ready": False}
    mail.ready = lambda: open_mail["ready"]
    sup = supervised(cfg, gateway, mail, applier, database)
    assert advance(sup, lambda: applier.state == "up" and mail.state == "starting")
    for _ in range(5):
        sup.tick(time.monotonic())
    assert gateway.proc is None, "it waits for everything it names in `after`"
    assert sup.phase == "starting", "one thing to open: not up while part of it is not"
    open_mail["ready"] = True
    assert advance(sup, lambda: gateway.state == "up")
    sup.tick(time.monotonic())
    assert sup.phase == "running"
    sup.shutdown()


def test_several_services_stop_in_reverse_with_the_database_last(cfg, tmp_path, monkeypatch):
    monkeypatch.setattr(pg, "ensure_database", lambda *_: None)
    order = tmp_path / "order"
    database, applier, mail, gateway = several(cfg, tmp_path, order=order)
    trap = f"trap 'echo postgres >> {order}; exit 0' INT; while :; do sleep 0.1; done"
    database.argv = ["sh", "-c", trap]
    sup = supervised(cfg, database, applier, mail, gateway)
    assert advance(sup, lambda: gateway.state == "up")
    time.sleep(0.4)
    sup.stop_reason = "Stopped by request."
    sup.shutdown()
    stopped = order.read_text().split()
    assert stopped[0] == "gateway" and stopped[-1] == "postgres"
    assert set(stopped) == {"gateway", "applier", "mail", "postgres"}
    assert supervisor.read_state(cfg)["stop_order"][0] == "gateway"


def test_only_the_one_that_died_restarts_unless_it_named_the_other(cfg, tmp_path, monkeypatch):
    monkeypatch.setattr(pg, "ensure_database", lambda *_: None)
    database, applier, mail, gateway = several(cfg, tmp_path)
    mail.restart_with = ("applier",)
    sup = supervised(cfg, database, applier, mail, gateway)
    assert advance(sup, lambda: gateway.state == "up")
    gateway_pid, mail_pid, applier_pid = gateway.pid, mail.pid, applier.pid
    os.kill(gateway_pid, signal.SIGKILL)
    assert advance(sup, lambda: gateway.pid not in (None, gateway_pid) and gateway.state == "up")
    assert mail.pid == mail_pid and applier.pid == applier_pid, "nothing else was touched"
    os.kill(applier_pid, signal.SIGKILL)
    assert advance(sup, lambda: mail.pid not in (None, mail_pid) and mail.state == "up", seconds=8)
    assert gateway.state == "up", "a `restart_with` is opt-in"
    sup.shutdown()


def test_restart_policies(cfg, tmp_path):
    ok_once = child("ok-once", "exit 0", restart="on-failure", service=True)
    never_ok = child("never-ok", "exit 0", restart="never", service=True)
    optional = child("never-bad", "exit 4", restart="never", service=True, required=False)
    after_done = child("later", "sleep 30", service=True, after=(ok_once,))
    sup = supervised(cfg, ok_once, never_ok, optional, after_done)
    assert advance(sup, lambda: after_done.state == "up" and optional.state == "failed")
    assert ok_once.state == "done" and ok_once.restarts == 0, "exit 0 under on-failure is finished, and counts as ready"
    assert never_ok.state == "done"
    assert not sup.stop_reason and "status 4" in optional.message
    sup.tick(time.monotonic())
    assert sup.phase == "running", "an optional service that failed does not hold up the rest"
    sup.shutdown()


def test_a_required_service_under_never_stops_everything(cfg):
    critical = child("critical", "exit 4", restart="never", service=True)
    sup = supervised(cfg, critical)
    assert advance(sup, lambda: sup.stop_reason)
    assert "critical is not running" in sup.stop_reason and "restart policy is never" in sup.stop_reason
    sup.shutdown()


def test_on_failure_restarts_a_crash(cfg, monkeypatch):
    monkeypatch.setattr(supervisor, "STABLE_SECONDS", 0.2)
    flaky = child("flaky", "sleep 0.4; exit 3", restart="on-failure", service=True)
    sup = supervised(cfg, flaky)
    assert advance(sup, lambda: flaky.restarts >= 2)
    assert not sup.stop_reason
    sup.shutdown()


def test_a_restart_request_names_what_it_restarts(cfg, tmp_path, monkeypatch):
    monkeypatch.setattr(pg, "ensure_database", lambda *_: None)
    database, applier, mail, gateway = several(cfg, tmp_path)
    sup = supervised(cfg, database, applier, mail, gateway)
    assert advance(sup, lambda: gateway.state == "up")
    before = {c.name: c.pid for c in sup.children}
    (cfg.home / "restart-request").write_text('["mail"]')
    sup.on_restart()
    assert advance(sup, lambda: mail.pid not in (None, before["mail"]) and mail.state == "up")
    assert gateway.pid == before["gateway"] and applier.pid == before["applier"] and database.pid == before["postgres"]
    assert not (cfg.home / "restart-request").exists()
    sup.on_restart()  # No names: every service, and not the database.
    assert advance(
        sup, lambda: gateway.pid not in (None, before["gateway"]) and applier.pid not in (None, before["applier"]) and gateway.state == "up"
    )
    assert database.pid == before["postgres"]
    sup.shutdown()


def test_each_service_has_its_own_log_and_health(cfg, tmp_path, monkeypatch):
    monkeypatch.setattr(pg, "find_bin", lambda _: Path("/nonexistent"))
    monkeypatch.setattr(pg, "init_cluster", lambda *_: False)
    text = ONE + 'after = ["postgres", "worker"]\nrestart_with = ["postgres"]\ndrains = true\n[service.env]\nROLE = "edge"\n'
    path = write_list(tmp_path, text)
    env = {"POSTGRES_PASSWORD": "synthetic", "AXIOM_SERVICES": str(path), "DATA_DIR": str(tmp_path / "data")}
    cfg = prepare(load(tmp_path, environ=env))
    sup = Supervisor(cfg, say=lambda _: None)
    children = sup.plan()
    assert [c.name for c in children] == ["postgres", "gateway"], "no worker Chrome is configured here"
    gateway = children[1]
    assert gateway.log == cfg.logs / "gateway.log" and gateway.health_url == "http://127.0.0.1:8090/healthz"
    assert [d.name for d in gateway.after] == ["postgres"], "a name this installation does not run is nothing to wait for"
    assert gateway.grace == cfg.stop_grace, "drains: the same grace as compose.yaml"
    assert gateway.restart_with == ("postgres",)
    environment = gateway.env()
    assert environment["ROLE"] == "edge" and environment["APP_PORT"] == "8090"
    assert environment["DATABASE_URL"] == cfg.database_url


def test_a_stop_after_a_dead_supervisor_follows_the_recorded_order(cfg):
    first = subprocess.Popen(["sleep", "60"], start_new_session=True)
    second = subprocess.Popen(["sleep", "60"], start_new_session=True)
    state = {
        "stop_order": ["gateway", "postgres"],
        "children": {
            "postgres": {"pid": second.pid, "lstart": supervisor.process_start(second.pid), "stop_signal": int(signal.SIGTERM), "grace": 5},
            "gateway": {"pid": first.pid, "lstart": supervisor.process_start(first.pid), "stop_signal": int(signal.SIGTERM), "grace": 5},
        },
    }
    lines = []
    for process in (first, second):
        Thread(target=process.wait, daemon=True).start()
    assert supervisor.stop_leftovers(cfg, state, say=lines.append)
    assert "gateway" in lines[0] and "postgres" in lines[1]
