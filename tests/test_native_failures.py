"""What the Mac throws at the supervisor while it runs: a full disk, a Mac just awake, a charger pulled out. With fakes, as
test_native_supervisor.py drives it: small shell children, and a `ps` or `pmset` that answers as it would then."""

import errno
import http.server
import json
import os
import pathlib
import signal
import subprocess
import sys
import threading
import time

import pytest

from jobpilot.native import backups, desktop, pg, supervisor
from jobpilot.native.env import NativeError, load, prepare
from jobpilot.native.supervisor import Child, Supervisor

FULL = OSError(errno.ENOSPC, "No space left on device")


@pytest.fixture
def cfg(tmp_path):
    (tmp_path / ".env").write_text(f"POSTGRES_PASSWORD=synthetic\nDATA_DIR={tmp_path}/data\nAPP_PORT=59183\nAXIOM_PG_PORT=59184\n")
    return prepare(load(tmp_path, environ={}))


def child(name, script, **kwargs):
    kwargs.setdefault("ready", lambda: True)
    return Child(name, ["sh", "-c", script], **kwargs)


def supervised(cfg, *children):
    sup = Supervisor(cfg, list(children), say=lambda line: sup.lines.append(line))
    sup.lines = []
    sup.next_tick = {"sleep": 1e18, "power": 1e18, "rotate": 1e18, "backup": 1e18, "chrome_update": 1e18}
    return sup


def advance(sup, until, seconds=6.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        sup.tick(time.monotonic())
        if until():
            return True
        time.sleep(0.05)
    return False


def never_becomes_the_supervisor(monkeypatch):
    """boot() ends in exec. A start that wrongly went ahead must fail this test, not replace the test run with a real supervisor."""

    def refuse(*_a, **_k):
        raise AssertionError("boot went on to start the supervisor")

    monkeypatch.setattr(desktop, "_detach", refuse)
    monkeypatch.setattr(desktop.os, "execv", refuse)


def disk_is_full(monkeypatch):
    """Writing the state file and every other text file fails, as it does with nothing free."""

    def refuse(*_a, **_k):
        raise FULL

    monkeypatch.setattr(pathlib.Path, "write_text", refuse)


# ---- a full disk ------------------------------------------------------------------------------------------------------


def test_a_stop_on_a_full_disk_still_stops_everything_in_order(cfg, tmp_path, monkeypatch):
    """shutdown() opened by writing the state file. With the disk full that raised before a single child was stopped: the supervisor
    died and left the app and the database running with nothing watching them."""
    order = tmp_path / "order"

    def named(name, signal_name):
        return f"trap 'echo {name} >> {order}; exit 0' {signal_name}; touch {tmp_path / name}; while :; do sleep 0.1; done"

    monkeypatch.setattr(pg, "ensure_database", lambda *_: None)
    database = child("postgres", named("postgres", "INT"), stop_signal=signal.SIGINT, grace=5.0)
    app = child("app", named("app", "TERM"), after=(database,), grace=5.0)
    sup = supervised(cfg, database, app)
    assert advance(sup, lambda: app.state == "up" and (tmp_path / "app").exists() and (tmp_path / "postgres").exists())
    with monkeypatch.context() as full:
        disk_is_full(full)
        sup.stop_reason = "Stopped by request."
        sup.shutdown()
    assert order.read_text().split() == ["app", "postgres"]
    assert app.proc is None and database.proc is None and sup.phase == "stopped"


def test_a_full_disk_leaves_the_last_whole_state_file_and_no_half_one(cfg, monkeypatch):
    sup = supervised(cfg)
    sup.write_state(force=True)
    before = cfg.state_file.read_text()
    with monkeypatch.context() as full:
        disk_is_full(full)
        sup.phase = "running"
        sup.write_state(force=True)
    assert cfg.state_file.read_text() == before
    assert not cfg.state_file.with_suffix(".tmp").exists()
    assert any("could not be written" in line for line in sup.lines)


def test_a_crash_on_a_full_disk_is_still_restarted(cfg, monkeypatch):
    worker = child("worker", "sleep 30", url="http://127.0.0.1:1")
    sup = supervised(cfg, worker)
    assert advance(sup, lambda: worker.state == "up")
    first = worker.pid
    with monkeypatch.context() as full:
        disk_is_full(full)
        os.kill(first, signal.SIGKILL)
        assert advance(sup, lambda: worker.pid not in (None, first) and worker.state == "up")
        sup.stop_reason = "Stopped by request."
        sup.shutdown()


def test_the_log_refusing_a_line_never_stops_the_supervisor(monkeypatch):
    class Refusing:
        def write(self, _):
            raise FULL

        def flush(self):
            raise FULL

    monkeypatch.setattr(sys, "stdout", Refusing())
    supervisor._say("anything")


def test_the_daily_backup_on_a_full_disk_costs_nothing_but_the_copy(cfg, monkeypatch):
    with monkeypatch.context() as full:
        disk_is_full(full)
        backups.record(cfg, error="no room")  # The one place that says why, and it can fail for the same reason.


def test_a_required_service_lost_to_a_full_disk_says_the_disk_is_full(cfg, monkeypatch):
    """The owner's friend sees this sentence, not "postgres exited 5 times in a row (status 1)"."""
    monkeypatch.setattr(supervisor, "MAX_STRIKES", 1)
    monkeypatch.setattr(supervisor.shutil, "disk_usage", lambda _: type("U", (), {"free": 5 * 1024 * 1024})())
    app = child("postgres", "exit 1", log=cfg.logs / "postgres.log")
    sup = supervised(cfg, app)
    assert advance(sup, lambda: sup.stop_reason, seconds=8)
    assert "disk is full" in sup.stop_reason and "Free some space" in sup.stop_reason
    assert "status 1" in app.message, "the log keeps the detail"
    sup.shutdown()


def test_with_room_on_the_disk_a_failure_keeps_its_own_words(cfg, monkeypatch):
    monkeypatch.setattr(supervisor, "MAX_STRIKES", 1)
    monkeypatch.setattr(supervisor.shutil, "disk_usage", lambda _: type("U", (), {"free": 50 * 1024**3})())
    app = child("app", "exit 3", log=cfg.logs / "app.log")
    sup = supervised(cfg, app)
    assert advance(sup, lambda: sup.stop_reason, seconds=8)
    assert "app is not running" in sup.stop_reason and "status 3" in sup.stop_reason
    sup.shutdown()


def test_a_start_with_no_room_says_so_and_starts_nothing(tmp_path, monkeypatch, capsys):
    layout = desktop.Layout(home=tmp_path / "home", resources=tmp_path / "AXIOM.app" / "Contents" / "Resources")
    never_becomes_the_supervisor(monkeypatch)
    monkeypatch.setattr(supervisor.shutil, "disk_usage", lambda _: type("U", (), {"free": 1024})())
    assert desktop.boot(layout) == 1
    told = json.loads(layout.progress_file.read_text())
    assert told["step"] == "failed" and "disk is full" in told["message"]
    assert not layout.env_file.exists(), "nothing was set up"


def test_a_start_that_cannot_even_report_still_ends_with_its_message(tmp_path, monkeypatch, capsys):
    """Nothing can be written at all: the window then looks at the disk itself. The failure must not become a traceback about boot.json."""
    layout = desktop.Layout(home=tmp_path / "home", resources=tmp_path / "AXIOM.app" / "Contents" / "Resources")
    layout.native.mkdir(parents=True)
    never_becomes_the_supervisor(monkeypatch)

    def refuse(*_a, **_k):
        raise FULL

    monkeypatch.setattr(desktop.tempfile, "mkstemp", refuse)
    monkeypatch.setattr(supervisor.shutil, "disk_usage", lambda _: type("U", (), {"free": 1024})())
    assert desktop.boot(layout) == 1
    assert "disk is full" in capsys.readouterr().err


def test_a_private_write_that_fails_leaves_nothing_behind(tmp_path, monkeypatch):
    target = tmp_path / "boot.json"
    target.write_text("whole")

    class Refusing:
        def __init__(self, fd, *_):
            os.close(fd)

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def write(self, _):
            raise FULL

    monkeypatch.setattr(desktop.os, "fdopen", Refusing)
    with pytest.raises(OSError):
        desktop.write_private(target, "new")
    assert [p.name for p in tmp_path.iterdir()] == ["boot.json"] and target.read_text() == "whole"


# ---- a Mac just awake, or short of everything -----------------------------------------------------------------------------


def owned(tmp_path, pid, started):
    (tmp_path / ".env").write_text(f"POSTGRES_PASSWORD=synthetic\nDATA_DIR={tmp_path}/data\n")
    return prepare(load(tmp_path, environ={"AXIOM_OWNER_PID": str(pid), "AXIOM_OWNER_LSTART": started}))


@pytest.mark.parametrize("ps", ["timeout", "empty", "oserror"])
def test_a_ps_that_fails_is_not_the_window_going_away(tmp_path, monkeypatch, ps):
    """Answered as "gone", one slow `ps` on a Mac just awake stopped every application; a timeout killed the supervisor outright."""
    sup = Supervisor(owned(tmp_path, os.getpid(), supervisor.process_start(os.getpid())), [], say=lambda _: None)

    def answer(*_a, **_k):
        if ps == "timeout":
            raise subprocess.TimeoutExpired("ps", 5)
        if ps == "oserror":
            raise OSError(errno.EAGAIN, "Resource temporarily unavailable")
        return ""

    monkeypatch.setattr(supervisor, "process_start", answer)
    sup.tick_owner()
    assert not sup.stop_reason


def test_a_ps_that_fails_as_a_child_starts_never_starts_it_twice(cfg, monkeypatch):
    """The process was running by then. With the child still marked down, the next tick started another and lost the first."""
    app = child("app", "sleep 30")
    sup = supervised(cfg, app)
    started = []
    real = subprocess.Popen

    def counted(argv, *a, **k):
        process = real(argv, *a, **k)
        if argv[0] == "sh":
            started.append(process)
        return process

    monkeypatch.setattr(supervisor.subprocess, "Popen", counted)
    answers = iter([subprocess.TimeoutExpired("ps", 5)])
    true_start = supervisor.process_start

    def flaky(pid):
        for failure in answers:
            raise failure
        return true_start(pid)

    monkeypatch.setattr(supervisor, "process_start", flaky)
    sup.tick(time.monotonic())
    assert app.state in {"starting", "up"} and len(started) == 1
    assert advance(sup, lambda: app.lstart, seconds=2), "asked again once `ps` answers, for `axiom stop` after a dead supervisor"
    assert len(started) == 1
    sup.stop_reason = "Stopped by request."
    sup.shutdown()


def test_a_chrome_script_that_hangs_counts_against_it_and_does_not_stop_the_rest(cfg, monkeypatch):
    def hung():
        raise subprocess.TimeoutExpired("sh", 30)

    twin = Child("twin", hung, ready=lambda: True, required=False, url="http://127.0.0.1:1")
    app = child("app", "sleep 30")
    sup = supervised(cfg, twin, app)
    assert advance(sup, lambda: app.state == "up" and twin.strikes >= 1)
    assert not sup.stop_reason
    sup.stop_reason = "Stopped by request."
    sup.shutdown()


class Garbled(http.server.BaseHTTPRequestHandler):
    def handle(self):
        self.request.recv(1024)
        self.request.sendall(b"this is not HTTP at all\r\n\r\n")


def test_something_else_on_a_port_is_a_no_and_not_a_crash():
    """`ready` runs inside the loop, and http.client's own errors are not OSErrors."""
    server = http.server.HTTPServer(("127.0.0.1", 0), Garbled)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        assert supervisor.http_ok(f"http://127.0.0.1:{server.server_port}/json/version") is False
    finally:
        server.shutdown()
        server.server_close()


def run_main(cfg, tick, monkeypatch):
    """Supervisor.main, on no children and no real checks. Its signal handlers are put back."""
    monkeypatch.setattr(supervisor, "docker_instances", lambda: [])
    monkeypatch.setattr(supervisor, "stop_leftovers", lambda *a, **k: False)
    sup = Supervisor(cfg, [child("idle", "sleep 30")], say=lambda line: lines.append(line))
    lines = []
    monkeypatch.setattr(sup, "tick", lambda now: tick(sup))
    kept = {sig: signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP, signal.SIGUSR1)}
    try:
        return sup, sup.main(), lines
    finally:
        for sig, handler in kept.items():
            signal.signal(sig, handler)


@pytest.mark.parametrize(
    "failure",
    [subprocess.TimeoutExpired("psql", 5), FULL, ValueError("The database choice is 'x'")],
    ids=["timeout", "disk-full", "unreadable-choice"],
)
def test_one_check_that_fails_does_not_stop_applications(cfg, monkeypatch, failure):
    calls = []

    def tick(sup):
        calls.append(1)
        if len(calls) == 1:
            raise failure
        sup.stop_reason = "Stopped by request."

    sup, code, lines = run_main(cfg, tick, monkeypatch)
    assert len(calls) == 2 and code == 0 and sup.reason == "Stopped by request."
    assert any("tried again" in line for line in lines)


def test_a_bug_in_the_loop_still_drains_and_tells_the_window_it_was_no_plain_stop(cfg, monkeypatch):
    """Phase stopped with no reason is what the window shows as "AXIOM was stopped, and nothing was lost"."""

    def tick(_):
        raise KeyError("a bug")

    sup, code, _ = run_main(cfg, tick, monkeypatch)
    state = supervisor.read_state(cfg)
    assert code == 1 and state["phase"] == "stopped" and "hit a problem (KeyError)" in state["reason"]


def test_the_quit_screen_never_fails_over_what_it_says(cfg, monkeypatch):
    sup = supervised(cfg)
    assert sup.read_json(supervisor.FINISHING, []) == [], "no psql on this installation"
    sup.pg_bin = pathlib.Path("/nonexistent")

    def refuse(*_a, **_k):
        raise NativeError("The saved Supabase connection cannot be read.")

    monkeypatch.setattr(pg, "psql", refuse)
    assert sup.read_json(supervisor.FINISHING, []) == []
    assert sup.batch_running() is False


def test_a_child_whose_watch_fails_does_not_keep_the_database_from_stopping(cfg, monkeypatch, tmp_path):
    monkeypatch.setattr(pg, "ensure_database", lambda *_: None)
    database = child("postgres", f"trap 'exit 0' INT; touch {tmp_path / 'pg'}; while :; do sleep 0.1; done", stop_signal=signal.SIGINT, grace=5.0)
    app = child("app", "sleep 30", after=(database,), grace=1.0)
    sup = supervised(cfg, database, app)
    assert advance(sup, lambda: app.state == "up" and (tmp_path / "pg").exists())
    real, pid = sup.stop_and_wait, app.pid

    def broken(which):
        if which is app:
            which.signal(signal.SIGKILL)
            which.proc.wait()
            raise KeyError("a bug")
        real(which)

    monkeypatch.setattr(sup, "stop_and_wait", broken)
    sup.stop_reason = "Stopped by request."
    sup.shutdown()
    assert database.proc is None and sup.phase == "stopped" and pid


# ---- battery and mains -----------------------------------------------------------------------------------------------


def pmset(monkeypatch, out):
    monkeypatch.setattr(supervisor.shutil, "which", lambda name: "/usr/bin/pmset")
    monkeypatch.setattr(supervisor.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout=out, stderr=""))


def test_a_desktop_mac_is_on_mains_with_no_percentage(monkeypatch):
    pmset(monkeypatch, "Now drawing from 'AC Power'\n")
    assert supervisor.power_status() == {"battery": False, "percent": None, "remaining": ""}


def test_a_charging_laptop_is_on_mains(monkeypatch):
    pmset(monkeypatch, "Now drawing from 'AC Power'\n -InternalBattery-0 (id=1)\t80%; charging; 0:40 remaining present: true\n")
    assert supervisor.power_status() == {"battery": False, "percent": 80, "remaining": "0:40"}


def test_pmset_with_no_answer_is_unknown_not_mains(monkeypatch):
    pmset(monkeypatch, "")
    assert supervisor.power_status() == {}


def test_the_charger_is_noted_once_each_way_and_a_silent_pmset_changes_nothing(cfg, monkeypatch):
    """Read as mains, one silent pmset made the window warn about the battery a second time, and the batch keep the Mac awake on battery."""
    sup = supervised(cfg)
    answers = iter(
        [
            {"battery": False, "percent": 90, "remaining": ""},
            {"battery": True, "percent": 89, "remaining": "2:00"},
            {},
            {"battery": True, "percent": 88, "remaining": "1:58"},
            {"battery": False, "percent": 88, "remaining": ""},
            {},
        ]
    )
    monkeypatch.setattr(supervisor, "power_status", lambda: next(answers))
    seen = []
    for _ in range(6):
        sup.tick_power()
        seen.append(sup.power.get("battery"))
    assert seen == [False, True, True, True, False, False]
    assert sum("Running on battery" in line for line in sup.lines) == 1
    assert sum("Back on power" in line for line in sup.lines) == 1


def test_a_mac_with_no_pmset_is_simply_unknown(cfg, monkeypatch):
    monkeypatch.setattr(supervisor, "power_status", lambda: {})
    sup = supervised(cfg)
    sup.tick_power()
    assert sup.power == {} and not sup.lines


def test_diagnostics_on_a_desktop_mac_read_plainly(tmp_path):
    layout = desktop.Layout(home=tmp_path, resources=tmp_path / "AXIOM.app" / "Contents" / "Resources")
    layout.native.mkdir(parents=True)
    (layout.native / "state.json").write_text(json.dumps({"phase": "running", "power": {"battery": False, "percent": None, "remaining": ""}}))
    text = desktop.diagnostics(layout)
    assert "Power: plugged in\n" in text and "None" not in text.split("Power:")[1].splitlines()[0]


# ---- sleep ---------------------------------------------------------------------------------------------------------------


class FakeCaffeinate:
    started = []

    def __init__(self, argv, **_):
        FakeCaffeinate.started.append(argv)
        self.running = True

    def poll(self):
        return None if self.running else 0

    def terminate(self):
        self.running = False

    def wait(self):
        return 0


def awake_rig(cfg, monkeypatch):
    FakeCaffeinate.started = []
    sup = supervised(cfg, child("postgres", "sleep 1"))
    sup.children[0].state = "up"
    sup.pg_bin = pathlib.Path("/nonexistent")
    monkeypatch.setattr(supervisor.subprocess, "Popen", FakeCaffeinate)
    monkeypatch.setattr(supervisor.shutil, "which", lambda name: "/usr/bin/" + name)
    return sup


def test_waking_with_the_database_slow_keeps_the_hold_rather_than_dropping_it(cfg, monkeypatch):
    sup = awake_rig(cfg, monkeypatch)
    answers = {"active": "2"}

    def psql(_bin, _cfg, sql, **_):
        if answers["active"] == "slow":
            raise subprocess.TimeoutExpired("psql", 5)
        return answers["active"] if sql == supervisor.ACTIVE_RUNS else "{}"

    monkeypatch.setattr(pg, "psql", psql)
    sup.tick_sleep(100.0)
    assert sup.caffeinate is not None and FakeCaffeinate.started[0][:3] == ["caffeinate", "-i", "-m"]
    answers["active"] = "slow"
    sup.tick_sleep(100.0 + supervisor.SLEEP_RELEASE_SECONDS + 500)
    assert sup.caffeinate is not None and sup.active is None, "unknown keeps what it had"
    answers["active"] = "2"
    sup.tick_sleep(100.0 + supervisor.SLEEP_RELEASE_SECONDS + 520)
    assert sup.caffeinate is not None and len(FakeCaffeinate.started) == 1


def test_applications_hold_the_mac_awake_on_battery_too_and_a_batch_alone_does_not(cfg, monkeypatch):
    sup = awake_rig(cfg, monkeypatch)
    sup.power = {"battery": True, "percent": 40}
    counts = {"active": "0", "batch": "1"}
    monkeypatch.setattr(pg, "psql", lambda _b, _c, sql, **_: counts["batch"] if sql == supervisor.BATCH_RUNNING else counts["active"] if sql == supervisor.ACTIVE_RUNS else "{}")
    sup.tick_sleep(100.0)
    assert sup.caffeinate is None, "a batch between applications, on battery: the Mac may sleep"
    counts["active"] = "1"
    sup.tick_sleep(120.0)
    assert sup.caffeinate is not None, "an application under way holds it, battery or not"


def test_time_asleep_is_not_time_up_on_this_clock():
    """Uptimes, drains and grace periods all use time.monotonic. On a Mac that clock stops during sleep, so a lid closed for a night neither
    counts as a child's stable minute nor runs out a drain's 180 s while nothing could finish."""
    if sys.platform == "darwin":
        assert time.get_clock_info("monotonic").implementation == "mach_absolute_time()"


# ---- what stops in what order, from the shipped list ---------------------------------------------------------------------


def test_the_shipped_list_stops_the_app_first_chrome_next_and_the_database_last(tmp_path, monkeypatch):
    monkeypatch.setattr(pg, "find_bin", lambda _: pathlib.Path("/nonexistent"))
    monkeypatch.setattr(pg, "init_cluster", lambda *_: False)
    env = {
        "POSTGRES_PASSWORD": "synthetic",
        "DATA_DIR": str(tmp_path / "data"),
        "BROWSER_CDP_URL": "http://127.0.0.1:59185",
        "BROWSER_TWIN_CDP_URL": "http://127.0.0.1:59186",
    }
    sup = Supervisor(prepare(load(tmp_path, environ=env)), say=lambda _: None)
    children = sup.plan()
    stop = [c.name for c in reversed(supervisor.in_dependency_order(children))]
    assert stop[0] == "app" and stop[-1] == "postgres" and set(stop[1:3]) == {"worker", "twin"}
    by_name = {c.name: c for c in children}
    assert by_name["postgres"].stop_signal == signal.SIGINT and by_name["postgres"].final_signal == signal.SIGQUIT
    assert by_name["app"].stop_signal == signal.SIGTERM and by_name["app"].grace == 180.0
    assert by_name["worker"].grace == supervisor.CHROME_GRACE and not by_name["twin"].required
