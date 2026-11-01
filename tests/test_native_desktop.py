"""AXIOM as an installed Mac app: where its config and secrets live, how it picks ports and finds Chrome, and how a checkout
install moves in without the source being touched. Everything runs in a scratch HOME with synthetic data."""

import http.server
import importlib.util
import json
import os
import plistlib
import shutil
import signal
import socket
import stat
import subprocess
import sys
import threading
import time
import zipfile
from pathlib import Path

import pytest

from jobpilot.features.resumes import editor
from jobpilot.native import desktop, movein, pg
from jobpilot.native.desktop import Layout
from jobpilot.native.env import NativeError, load, prepare
from jobpilot.native.supervisor import Child, Supervisor, chrome_argv, process_start, read_state

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def layout(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    resources = tmp_path / "AXIOM.app" / "Contents" / "Resources"
    (resources / "app").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    # What a test sets must not outlive it: boot and move-in write these into os.environ, as the real process does.
    saved = dict(os.environ)
    yield Layout(home=home, resources=resources)
    os.environ.clear()
    os.environ.update(saved)


def mode(path):
    return stat.S_IMODE(os.stat(path).st_mode)


def test_config_and_secrets_are_created_once_outside_the_bundle_and_private(layout):
    assert desktop.ensure_config(layout) is True
    first = layout.env_file.read_text()
    assert mode(layout.env_file) == 0o600 and mode(layout.support) == 0o700
    assert str(layout.resources) not in str(layout.env_file), "an upgrade replaces the bundle, and the signature seals it"
    assert "POSTGRES_PASSWORD=" in first
    assert desktop.ensure_config(layout) is False, "this password is what the database was created with"
    assert layout.env_file.read_text() == first


def test_each_install_gets_its_own_database_password():
    assert desktop.first_config(32).split("POSTGRES_PASSWORD=")[1][:48] != desktop.first_config(32).split("POSTGRES_PASSWORD=")[1][:48]


def test_a_small_mac_gets_the_worker_counts_it_can_carry():
    assert "NORMAL_WORKERS=3" in desktop.first_config(16)
    assert "NORMAL_WORKERS" not in desktop.first_config(32)


def test_ports_are_free_loopback_and_never_what_production_uses(layout):
    ports = desktop.pick_ports(layout)
    assert sorted(ports) == sorted(desktop.PORTS) and len(set(ports.values())) == 4
    assert not desktop.AVOID_PORTS.intersection(ports.values())
    assert {8080, 9224, 9225, 9339}.issubset(desktop.AVOID_PORTS)
    assert mode(layout.ports_file) == 0o600


def test_ports_stay_the_same_between_starts_because_the_pages_preferences_belong_to_its_address(layout):
    assert desktop.pick_ports(layout) == desktop.pick_ports(layout)


def test_a_port_someone_else_took_is_replaced_and_the_rest_are_kept(layout):
    first = desktop.pick_ports(layout)
    with socket.socket() as holder:
        holder.bind(("127.0.0.1", first["app"]))
        holder.listen()
        second = desktop.pick_ports(layout)
    assert second["app"] != first["app"]
    assert {k: second[k] for k in ("postgres", "worker", "twin")} == {k: first[k] for k in ("postgres", "worker", "twin")}


def test_a_saved_port_production_uses_is_not_trusted(layout):
    desktop.write_private(layout.ports_file, json.dumps({"app": 8080, "postgres": 54329, "worker": 9224, "twin": 9225}))
    assert not desktop.AVOID_PORTS.intersection(desktop.pick_ports(layout).values())


def test_the_environment_has_no_homebrew_and_the_app_keeps_it_that_way(layout, tmp_path):
    desktop.ensure_config(layout)
    env = desktop.environment(layout, {"app": 50001, "postgres": 50002, "worker": 50003, "twin": 50004}, "/fake/chrome")
    assert "homebrew" not in env["PATH"] and "/usr/local" not in env["PATH"] and "/Library/TeX" not in env["PATH"]
    assert env["OPENSSL_CONF"].startswith(str(layout.resources)), "the bundled OpenSSL must not read Homebrew's config"
    assert env["PATH"].split(os.pathsep)[0] == str(layout.tex_bin), "pdflatex is the bundle's, whatever else is on the Mac"
    cfg = load(environ=env)
    assert cfg.root == layout.support and cfg.data_dir == layout.data and cfg.logs == layout.logs
    assert cfg.base_url == "http://127.0.0.1:50001" and cfg.pg_port == 50002
    assert cfg.worker_cdp == "http://127.0.0.1:50003" and cfg.twin_cdp == "http://127.0.0.1:50004"
    assert cfg.path_with_tools(env) == env["PATH"]
    assert "homebrew" in load(tmp_path, environ={}).path_with_tools({"PATH": "/usr/bin"}), "a checkout still finds its tools"


@pytest.mark.regression
def test_a_folder_holding_its_own_jobpilot_cannot_replace_the_apps(layout, tmp_path):
    """The first launch beside production ran from the checkout's folder, and Python imported that checkout's `jobpilot`, the
    working directory being first on sys.path (2026-10-31). It failed only because that one had no desktop.py."""
    assert desktop.environment(layout)["PYTHONSAFEPATH"] == "1"
    decoy = tmp_path / "decoy" / "jobpilot"
    decoy.mkdir(parents=True)
    (decoy / "__init__.py").write_text("raise SystemExit('imported the wrong package')")
    env = {"PATH": "/usr/bin:/bin", "HOME": str(layout.home), "PYTHONPATH": str(ROOT), "PYTHONSAFEPATH": "1"}
    done = subprocess.run(
        [sys.executable, "-m", "jobpilot.native.desktop", "login-item"], cwd=decoy.parent, env=env, capture_output=True, text=True, timeout=30
    )
    assert done.returncode == 0 and done.stdout.strip() == "off", done.stderr
    del env["PYTHONSAFEPATH"]
    done = subprocess.run(
        [sys.executable, "-m", "jobpilot.native.desktop", "login-item"], cwd=decoy.parent, env=env, capture_output=True, text=True, timeout=30
    )
    assert "wrong package" in done.stderr, "without it, the folder wins: this is what the setting is for"


def test_a_bug_while_starting_reaches_the_window_as_words(layout, monkeypatch, capsys):
    monkeypatch.setattr(desktop, "_boot", lambda *_: 1 / 0)
    assert desktop.boot(layout) == 1
    report = json.loads(layout.progress_file.read_text())
    assert report["step"] == "failed" and "ZeroDivisionError" in report["message"]
    assert "Traceback" in capsys.readouterr().err, "the log says what"


def test_the_chrome_scripts_travel_with_the_code_not_the_config_folder(layout):
    desktop.ensure_config(layout)
    shutil.copytree(ROOT / "scripts", layout.app / "scripts")
    env = {**desktop.environment(layout, {"app": 50001, "postgres": 50002, "worker": 50003, "twin": 50004}), "CHROME_BIN": "/fake/chrome"}
    cfg = load(environ=env)
    os.environ.update(env)
    argv = chrome_argv(cfg, "chrome-worker.sh", "CHROME_WORKER_PORT", "CHROME_WORKER_PROFILE", cfg.worker_cdp, layout.data / "w b")
    assert argv[0] == "/fake/chrome"
    assert "--remote-debugging-port=50003" in argv and f"--user-data-dir={layout.data}/w b" in argv


def test_the_persons_own_chrome_comes_first(layout, tmp_path):
    mine = tmp_path / "Mine" / "Google Chrome"
    mine.parent.mkdir()
    mine.write_text("#!/bin/sh\n")
    mine.chmod(0o755)
    layout.downloaded_chrome.parent.mkdir(parents=True)
    layout.downloaded_chrome.write_text("#!/bin/sh\n")
    layout.downloaded_chrome.chmod(0o755)
    assert desktop.find_chrome(layout, installed=(str(mine),)) == (str(mine), "installed")
    assert desktop.find_chrome(layout, installed=()) == (str(layout.downloaded_chrome), "downloaded")
    layout.downloaded_chrome.unlink()
    assert desktop.find_chrome(layout, installed=()) == ("", "")


def test_a_chrome_in_the_persons_own_applications_folder_counts(layout):
    path = layout.home / "Applications" / "Google Chrome.app" / "Contents" / "MacOS" / "Google Chrome"
    path.parent.mkdir(parents=True)
    path.write_text("#!/bin/sh\n")
    path.chmod(0o755)
    assert desktop.find_chrome(layout, installed=("~/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",))[1] == "installed"


class Files(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *_):
        pass


@pytest.fixture
def mirror(tmp_path):
    """A local stand-in for Google's download servers, holding a synthetic Chrome."""
    root = tmp_path / "mirror"
    root.mkdir()
    handler = lambda *a, **k: Files(*a, directory=str(root), **k)  # noqa: E731
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield root, f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def publish(root, base, *, contains_chrome=True):
    with zipfile.ZipFile(root / "chrome.zip", "w") as z:
        inner = f"chrome-{desktop.chrome_platform()}/{desktop.CHROME_APP}/Contents/MacOS/Google Chrome for Testing"
        info = zipfile.ZipInfo(inner if contains_chrome else "chrome/readme.txt")
        info.external_attr = 0o755 << 16
        z.writestr(info, "#!/bin/sh\n" + "#" * 200000)
    (root / "index.json").write_text(
        json.dumps(
            {"channels": {"Stable": {"downloads": {"chrome": [{"platform": desktop.chrome_platform(), "url": f"{base}/chrome.zip"}]}}}}
        )
    )
    return f"{base}/index.json"


def test_a_mac_without_chrome_downloads_one_and_shows_its_progress(layout, mirror):
    index = publish(*mirror)
    seen = []
    layout.support.mkdir(parents=True)
    path = desktop.download_chrome(layout, lambda step, message, percent: seen.append((step, message, percent)), index=index)
    assert path == str(layout.downloaded_chrome) and os.access(path, os.X_OK)
    assert any(p == 100 for _, _, p in seen), "visible progress up to the end"
    assert any("MB" in m for _, m, _ in seen)
    assert not list(layout.downloaded_chrome.parents[3].glob(".download-*")), "nothing half-unpacked stays"


def test_a_failed_chrome_download_says_what_to_do_and_leaves_nothing(layout, mirror):
    root, base = mirror
    layout.support.mkdir(parents=True)
    with pytest.raises(NativeError, match="could not download it.*install Google Chrome"):
        desktop.download_chrome(layout, lambda *_: None, index=f"{base}/missing.json")
    with pytest.raises(NativeError, match="could not download"):
        desktop.download_chrome(layout, lambda *_: None, index=publish(root, base, contains_chrome=False))
    assert not layout.downloaded_chrome.exists()


def test_chrome_is_only_ever_taken_from_googles_download_host():
    assert desktop._allowed("https://storage.googleapis.com/chrome-for-testing-public/x.zip", desktop.CHROME_HOSTS)
    assert not desktop._allowed("https://evil.example/x.zip", desktop.CHROME_HOSTS)
    assert not desktop._allowed("http://storage.googleapis.com/x.zip", desktop.CHROME_HOSTS), "never over plain http"


def test_a_download_with_too_little_disk_says_so_before_starting(layout, monkeypatch):
    layout.support.mkdir(parents=True)
    monkeypatch.setattr(desktop, "CHROME_NEEDS_BYTES", 2**62)
    with pytest.raises(NativeError, match="free disk"):
        desktop.download_chrome(layout, lambda *_: None, index="http://127.0.0.1:1/never")


def test_the_windows_token_is_the_one_the_api_checks_and_stays_private(layout):
    desktop.ensure_config(layout)
    cfg = prepare(load(environ=desktop.environment(layout)))
    token = desktop.access_token(cfg)
    assert mode(cfg.data_dir / "access-token") == 0o600
    assert desktop.access_token(cfg) == token, "stable"
    from jobpilot.config import Settings

    settings = Settings(_env_file=None, data_dir=cfg.data_dir, app_token="", database_url="sqlite:///:memory:", dbos_database_url="sqlite:///:memory:")
    settings.prepare()
    assert settings.app_token == token, "the window signs in with what the API checks"


def test_an_app_token_in_config_wins_as_it_does_in_the_app(layout):
    desktop.write_private(layout.env_file, "POSTGRES_PASSWORD=x\nAPP_TOKEN=synthetic-token\n")
    assert desktop.access_token(prepare(load(environ=desktop.environment(layout)))) == "synthetic-token"


def test_start_at_login_is_one_file_that_opens_this_copy(layout):
    desktop.set_login_item(layout, True)
    item = plistlib.loads(layout.login_item.read_bytes())
    assert item["RunAtLoad"] is True and item["ProgramArguments"] == ["/usr/bin/open", "-a", str(layout.bundle)]
    desktop.set_login_item(layout, False)
    assert not layout.login_item.exists()
    desktop.set_login_item(layout, False)  # Turning it off twice is fine.


def test_start_at_login_follows_the_app_when_it_moves(layout):
    desktop.set_login_item(layout, True)
    moved = Layout(home=layout.home, resources=layout.home / "Applications" / "AXIOM.app" / "Contents" / "Resources")
    desktop.refresh_login_item(moved)
    assert plistlib.loads(layout.login_item.read_bytes())["ProgramArguments"][-1] == str(moved.bundle)


def test_turning_it_off_stays_off(layout):
    desktop.refresh_login_item(layout)
    assert not layout.login_item.exists()


def test_a_checkout_command_is_refused_rather_than_run_against_a_bundle(layout, capsys):
    assert desktop.main(["deploy"]) == 2
    assert "for a checkout install" in capsys.readouterr().err


def test_a_command_before_the_first_open_says_so(layout, capsys):
    os.environ["AXIOM_BUNDLE"] = str(layout.resources)
    assert desktop.main(["status"]) == 1
    assert "has not been opened yet" in capsys.readouterr().err


def test_boot_reports_a_failure_where_the_window_reads_it(layout, monkeypatch, capsys):
    monkeypatch.setattr(desktop, "_boot", lambda *_: (_ for _ in ()).throw(NativeError("synthetic reason")))
    assert desktop.boot(layout) == 1
    assert json.loads(layout.progress_file.read_text()) == {"step": "failed", "message": "synthetic reason", "percent": None}


@pytest.mark.regression
def test_the_app_will_not_start_beside_the_installation_it_was_moved_from(layout, tmp_path, monkeypatch):
    """Two databases, each believing it alone applies to these jobs, would send every application twice (FIX_BRIEF: a duplicate
    application is much worse than a false "not yet"). A moved-in checkout started again is exactly that."""
    old = tmp_path / "checkout"
    old.mkdir()
    desktop.write_private(layout.moved_from_file, json.dumps({"source": str(old)}))
    assert desktop.old_installation_running(layout) == ""
    monkeypatch.setattr(desktop.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(
        a, 0, f"/opt/pg/bin/postgres -D {old}/.data/postgres\n", ""))
    assert desktop.old_installation_running(layout) == str(old)
    monkeypatch.setattr(os, "execv", lambda *_: pytest.fail("the supervisor must not start"))
    assert desktop.boot(layout) == 1
    assert "still running" in json.loads(layout.progress_file.read_text())["message"]


@pytest.mark.regression
def test_the_old_folder_is_never_read_and_a_mere_mention_of_it_blocks_nothing(layout, tmp_path, monkeypatch):
    """A checkout on the Desktop sits behind a macOS privacy prompt: reading its .env froze the first start (2026-10-31)."""
    old = tmp_path / "checkout"
    old.mkdir()
    (old / ".env").write_text("POSTGRES_PASSWORD=x\n")
    (old / ".env").chmod(0)
    desktop.write_private(layout.moved_from_file, json.dumps({"source": str(old)}))
    monkeypatch.setattr(desktop, "load", lambda *a, **k: pytest.fail("the old folder must not be read"))
    table = f"vim {old}/notes.txt\ntail -f {old}/.data/native/logs/app.log\npython -m pytest {old}/tests\n"
    monkeypatch.setattr(desktop.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, table, ""))
    assert desktop.old_installation_running(layout) == ""
    chrome = f"Chrome --user-data-dir={old}/.data/desktop-browser --remote-debugging-port=9224\n"
    monkeypatch.setattr(desktop.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, chrome, ""))
    assert desktop.old_installation_running(layout) == str(old)


def owned_cfg(tmp_path, pid, started):
    (tmp_path / ".env").write_text(f"POSTGRES_PASSWORD=x\nDATA_DIR={tmp_path}/data\n")
    return prepare(load(tmp_path, environ={"AXIOM_OWNER_PID": str(pid), "AXIOM_OWNER_LSTART": started}))


@pytest.mark.regression
def test_the_engine_stops_safely_when_its_window_is_gone(tmp_path):
    """A force-quit must not leave an engine submitting real applications with nothing on screen."""
    window = subprocess.Popen(["sleep", "60"])
    sup = Supervisor(owned_cfg(tmp_path, window.pid, process_start(window.pid)), [], say=lambda _: None)
    sup.tick_owner()
    assert not sup.stop_reason, "the window is still there"
    window.send_signal(signal.SIGKILL)
    window.wait()
    sup.tick_owner()
    assert "stopped safely" in sup.stop_reason


def test_a_reused_pid_is_not_the_window(tmp_path):
    sup = Supervisor(owned_cfg(tmp_path, os.getpid(), "Thu Jan  1 00:00:00 1970"), [], say=lambda _: None)
    sup.tick_owner()
    assert sup.stop_reason


def test_a_checkout_install_has_no_owner_and_outlives_its_terminal(tmp_path):
    (tmp_path / ".env").write_text(f"POSTGRES_PASSWORD=x\nDATA_DIR={tmp_path}/data\n")
    sup = Supervisor(prepare(load(tmp_path, environ={})), [], say=lambda _: None)
    sup.tick_owner()
    assert not sup.stop_reason


def holding(profile):
    """A stand-in for Chrome: a process with the profile on its command line and Chrome's lock pointing at it."""
    profile.mkdir(parents=True, exist_ok=True)
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)", f"--user-data-dir={profile}"])
    os.symlink(f"somehost-{process.pid}", profile / "SingletonLock")
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and f"--user-data-dir={profile}" not in subprocess.run(
        ["ps", "-o", "command=", "-p", str(process.pid)], capture_output=True, text=True
    ).stdout:
        time.sleep(0.05)
    return process


@pytest.mark.regression
def test_a_chrome_that_relaunched_itself_does_not_outlive_quit(tmp_path):
    """A fresh worker profile's Chrome relaunched itself 143 s in (2026-10-31). The new process was nobody's child, read as
    `external`, and stayed running after Quit."""
    (tmp_path / ".env").write_text(f"POSTGRES_PASSWORD=x\nDATA_DIR={tmp_path}/data\n")
    cfg = prepare(load(tmp_path, environ={"AXIOM_BUNDLE": str(tmp_path)}))
    profile = cfg.data_dir / "desktop-browser"
    chrome = holding(profile)
    child = Child("worker", ["true"], url="http://127.0.0.1:1", profile=profile)
    child.state = "external"
    sup = Supervisor(cfg, [child], say=lambda _: None)
    sup.stop_reason = "test"
    sup.shutdown()
    assert chrome.wait(timeout=10) is not None


def test_a_checkout_still_leaves_a_chrome_it_did_not_start_alone(tmp_path):
    (tmp_path / ".env").write_text(f"POSTGRES_PASSWORD=x\nDATA_DIR={tmp_path}/data\n")
    cfg = prepare(load(tmp_path, environ={}))
    profile = cfg.data_dir / "desktop-browser"
    chrome = holding(profile)
    child = Child("worker", ["true"], url="http://127.0.0.1:1", profile=profile)
    child.state = "external"
    sup = Supervisor(cfg, [child], say=lambda _: None)
    sup.stop_reason = "test"
    sup.shutdown()
    try:
        assert chrome.poll() is None, "docs/NATIVE.md: external is left alone"
    finally:
        chrome.kill()
        chrome.wait()


def test_a_lock_whose_pid_was_reused_is_not_chrome(tmp_path):
    from jobpilot.native.supervisor import chrome_holding

    profile = tmp_path / "profile"
    profile.mkdir()
    os.symlink(f"somehost-{os.getpid()}", profile / "SingletonLock")
    assert chrome_holding(profile) is None, "that pid is this test, which must never be signalled"
    assert chrome_holding(tmp_path / "none") is None


def load_tex_bundler():
    spec = importlib.util.spec_from_file_location("bundle_tex", ROOT / "desktop" / "bundle_tex.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_a_tex_tree_is_listed_as_kpathsea_expects(tmp_path):
    """texmf.cnf marks these trees `!!`: kpathsea finds nothing in one except through its ls-R."""
    write(tmp_path / "tree" / "fonts" / "tfm" / "a.tfm")
    load_tex_bundler().write_ls_r(tmp_path / "tree")
    text = (tmp_path / "tree" / "ls-R").read_text()
    assert text.startswith("% ls-R -- filename database for kpathsea; do not change this line.\n./:\n")
    assert "\n./fonts/tfm:\na.tfm\n" in text


@pytest.mark.regression
def test_the_bundled_tex_has_no_trees_of_the_build_machines(tmp_path):
    """This Mac's TinyTeX config names R's folder, and every compile in the installed app read it (the clean-run trace, 2026-10-31)."""
    cnf = load_tex_bundler().ROOT_CNF
    assert "TEXMFAUXTREES =\n" in cnf and "TEXMFHOME = $TEXMFLOCAL" in cnf and "/Library" not in cnf


def test_every_template_and_font_is_bundled_for():
    """A template or font added to the editor must reach the build's recorder, or it fails on a Mac with no LaTeX."""
    names = [name for name, _ in load_tex_bundler().documents()]
    for template in editor.TEMPLATES:
        for font in editor.FONTS:
            assert any(name.startswith(f"{template}-{font}-") for name in names)
    assert "tailored" in names and "hand-written" in names


# ---- moving in, on a real scratch cluster --------------------------------------------------------------------------------


def write(path, content=b"synthetic", mtime=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    if mtime:
        os.utime(path, (mtime, mtime))


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def synthetic_checkout(root, bin_dir):
    """A stopped checkout install: `.env`, a real cluster holding a few rows, runs, keys, and a Chrome profile with a stale lock."""
    root.mkdir()
    port = free_port()
    (root / ".env").write_text(
        f"POSTGRES_PASSWORD=synthetic-password\nDATA_DIR=.data\nAPP_PORT=8080\nBROWSER_CDP_URL=http://127.0.0.1:9224\n"
        f"AXIOM_PG_PORT={port}\nCHROME_WORKER_PROFILE={root}/.data/desktop-browser\nOPENAI_MODEL=keep-this\n"
    )
    cfg = prepare(load(root, environ={}))
    pg.init_cluster(bin_dir, cfg)
    server = subprocess.Popen(pg.server_argv(bin_dir, cfg), env=pg.server_env(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.monotonic() + 30
        while not pg.is_ready(bin_dir, cfg):
            assert time.monotonic() < deadline
            time.sleep(0.2)
        pg.ensure_database(bin_dir, cfg)
        pg.psql(
            bin_dir,
            cfg,
            "create table settings(key text primary key, value json); create table runs(id text primary key, state text, packet json);"
            "create table jobs(id serial primary key, title text); insert into jobs(title) select 'Synthetic '||g from generate_series(1,250) g;"
            f"insert into runs values ('a','queued','{{\"resume\":\"{cfg.data_dir}/resumes/r.pdf\"}}'), ('b','confirmed','{{\"resume\":\"{cfg.data_dir}/resumes/r.pdf\"}}');",
        )
    finally:
        server.send_signal(signal.SIGINT)
        server.wait(timeout=30)
    write(cfg.data_dir / "mail-key", b"synthetic-key")
    os.chmod(cfg.data_dir / "mail-key", 0o600)
    write(cfg.data_dir / "connections.enc", b"sealed")
    write(cfg.data_dir / "resumes" / "r.pdf", b"%PDF-synthetic")
    write(cfg.home / "state.json", b"{}")
    for n in range(70):
        write(cfg.data_dir / "runs" / f"run{n}" / "final.png", b"\x89PNG" * 50, mtime=1_700_000_000 + n)
    (cfg.data_dir / "desktop-browser").mkdir()
    write(cfg.data_dir / "desktop-browser" / "Cookies", b"sqlite")
    os.symlink("somehost-999999", cfg.data_dir / "desktop-browser" / "SingletonLock")
    os.symlink("r.pdf", cfg.data_dir / "resumes" / "latest")
    return cfg


@pytest.fixture
def moving(layout, tmp_path):
    try:
        bin_dir = pg.find_bin(load(tmp_path, environ={}))
    except NativeError:
        pytest.skip("PostgreSQL 17 is not installed here")
    (layout.resources / "postgres").mkdir()
    os.symlink(bin_dir, layout.pg_bin)
    source = synthetic_checkout(tmp_path / "checkout", bin_dir)
    return layout, source, bin_dir


def query_copy(layout, sql):
    from jobpilot.native.cli import MigrationServer

    os.environ.update(desktop.existing_environment(layout))
    cfg = load(environ=os.environ)
    with MigrationServer(cfg) as server:
        return pg.psql(server.bin_dir, cfg, sql)


def test_moving_in_verifies_everything_and_never_touches_the_source(moving):
    layout, source, _ = moving
    before = movein.listing(source.data_dir)
    env_before = (source.root / ".env").read_bytes()
    lines = []
    assert movein.move(layout, source.root, say=lines.append) == 0
    assert movein.listing(source.data_dir) == before and (source.root / ".env").read_bytes() == env_before
    assert (source.home / "state.json").exists(), "the source keeps even what does not move"
    data = layout.data
    assert (data / "mail-key").read_bytes() == b"synthetic-key" and mode(data / "mail-key") == 0o600
    assert (data / "runs" / "run69" / "final.png").stat().st_mtime == 1_700_000_069
    assert os.readlink(data / "resumes" / "latest") == "r.pdf"
    assert not (data / "desktop-browser" / "SingletonLock").is_symlink(), "a dead Chrome's lock would stop the new one"
    assert not (data / "postgres" / "postmaster.pid").exists()
    report = json.loads((data / "migration" / "move-in.json").read_text())
    assert report["ok"] and report["tables"]["public.jobs"] == 250 and report["source"] == str(source.root)
    assert any("Verified" in line and "tables" in line for line in lines)
    assert mode(data / "migration" / "move-in.json") == 0o600


def test_the_moved_config_keeps_settings_and_drops_where_things_were(moving):
    layout, source, _ = moving
    movein.move(layout, source.root, say=lambda _: None)
    env = layout.env_file.read_text()
    assert mode(layout.env_file) == 0o600
    assert "POSTGRES_PASSWORD=synthetic-password" in env, "the cluster was created with it"
    assert "OPENAI_MODEL=keep-this" in env
    for gone in ("\nAPP_PORT=", "\nBROWSER_CDP_URL=", "\nAXIOM_PG_PORT=", "\nDATA_DIR=", "\nCHROME_WORKER_PROFILE="):
        assert gone not in "\n" + env, f"{gone.strip()} would point at the old installation"


@pytest.mark.regression
def test_the_moved_database_opens_held_with_its_queued_paths_rewritten(moving):
    """Nothing applies until the owner has looked, as `migrate db` leaves it (docs/NATIVE.md). A queued run must read its
    resume from the new folder; the old one may be gone by the time it runs."""
    layout, source, _ = moving
    movein.move(layout, source.root, say=lambda _: None)
    assert query_copy(layout, "select value->>'stage' from settings where key='execution_v2_deployment_hold'") == "draining"
    assert str(layout.data) in query_copy(layout, "select packet->>'resume' from runs where id='a'")
    assert str(source.data_dir) in query_copy(layout, "select packet->>'resume' from runs where id='b'"), "only queued runs need it"
    assert query_copy(layout, "select count(*) from jobs") == "250"


def test_moving_in_refuses_while_the_source_is_running(moving):
    layout, source, _ = moving
    source.state_file.write_text(json.dumps({"pid": os.getpid(), "lstart": process_start(os.getpid())}))
    with pytest.raises(NativeError, match="is running"):
        movein.move(layout, source.root, say=lambda _: None)
    assert not layout.data.exists()


def test_moving_in_refuses_while_a_chrome_is_using_a_profile(moving):
    layout, source, _ = moving
    lock = source.data_dir / "desktop-browser" / "SingletonLock"
    lock.unlink()
    os.symlink(f"somehost-{os.getpid()}", lock)
    with pytest.raises(NativeError, match="Chrome is still using"):
        movein.move(layout, source.root, say=lambda _: None)


def test_data_already_here_is_kept_never_deleted(moving):
    layout, source, _ = moving
    write(layout.data / "runs" / "earlier" / "final.png", b"earlier")
    desktop.write_private(layout.env_file, "POSTGRES_PASSWORD=earlier\n")
    with pytest.raises(NativeError, match="--replace"):
        movein.move(layout, source.root, say=lambda _: None)
    assert (layout.data / "runs" / "earlier").exists()
    assert movein.move(layout, source.root, replace=True, say=lambda _: None) == 0
    kept = list(layout.support.glob("data.before-move-*"))
    assert len(kept) == 1 and (kept[0] / "runs" / "earlier" / "final.png").read_bytes() == b"earlier"
    assert list(layout.support.glob(".env.before-move-*"))
    assert not (layout.data / "runs" / "earlier").exists()


def test_the_plan_changes_nothing(moving, capsys):
    layout, source, _ = moving
    before = movein.listing(source.data_dir)
    assert movein.main(layout, ["--from", str(source.root), "--plan"]) == 0
    assert not layout.support.exists() and movein.listing(source.data_dir) == before
    out = capsys.readouterr().out
    assert "APFS clones" in out and "synthetic-password" not in out, "names only: a plan's output is pasted around"
    assert "APP_PORT" in out and "CHROME_WORKER_PROFILE" in out and "Runs        70" in out


def test_a_plan_for_something_that_is_not_a_checkout_says_so(layout, tmp_path):
    with pytest.raises(NativeError, match="not a checkout"):
        movein.main(layout, ["--from", str(tmp_path), "--plan"])


def test_a_copy_that_differs_is_caught_at_every_level(tmp_path):
    source, copy = tmp_path / "s", tmp_path / "c"
    for root in (source, copy):
        write(root / "postgres" / "base" / "1", b"same", mtime=1_700_000_000)
        write(root / "runs" / "r" / "a.png", b"same", mtime=1_700_000_000)
    theirs = movein.listing(source)
    assert movein.compare(source, copy, movein.listing(copy), theirs, 10, say=lambda _: None) == []
    write(copy / "postgres" / "base" / "1", b"diff", mtime=1_700_000_000)
    assert "contents differ" in movein.compare(source, copy, movein.listing(copy), theirs, 10, say=lambda _: None)[0]
    write(copy / "runs" / "r" / "a.png", b"longer content", mtime=1_700_000_000)
    assert "in the copy" in movein.compare(source, copy, movein.listing(copy), theirs, 10, say=lambda _: None)[0]
    (copy / "runs" / "r" / "a.png").unlink()
    assert "only in the source" in movein.compare(source, copy, movein.listing(copy), theirs, 10, say=lambda _: None)[0]


def test_a_copy_stops_at_the_disk_floor_and_keeps_what_it_has(tmp_path):
    """A copy loop filled this disk once (2026-10-21)."""
    source = tmp_path / "s"
    write(source / "runs" / "r" / "a.png")
    with pytest.raises(NativeError, match="Nothing has been deleted"):
        movein.copy_tree(source, tmp_path / "c", say=lambda _: None, floor=2**62)
    assert (source / "runs" / "r" / "a.png").exists()


def test_a_stale_collation_is_rebuilt_as_postgresql_prescribes(moving):
    layout, source, bin_dir = moving
    from jobpilot.native.cli import MigrationServer

    cfg = load(source.root, environ={})
    with MigrationServer(cfg):
        assert movein.refresh_collation(bin_dir, cfg, say=lambda _: None) is False, "the same ICU: nothing to do"
        pg.psql(bin_dir, cfg, "update pg_database set datcollversion='0.0' where datname=current_database()")
        assert movein.refresh_collation(bin_dir, cfg, say=lambda _: None) is True
        assert movein.refresh_collation(bin_dir, cfg, say=lambda _: None) is False


def test_the_module_is_runnable_the_way_the_window_runs_it(layout):
    done = subprocess.run(
        [sys.executable, "-m", "jobpilot.native.desktop", "login-item"],
        cwd=ROOT,
        env={"HOME": str(layout.home), "PATH": "/usr/bin:/bin", "AXIOM_BUNDLE": str(layout.resources), "PYTHONPATH": str(ROOT)},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert done.returncode == 0 and done.stdout.strip() == "off", done.stderr


def test_state_written_by_a_supervisor_is_what_the_window_reads(tmp_path):
    (tmp_path / ".env").write_text(f"POSTGRES_PASSWORD=x\nDATA_DIR={tmp_path}/data\n")
    cfg = prepare(load(tmp_path, environ={}))
    Supervisor(cfg, [], say=lambda _: None).write_state(force=True)
    state = read_state(cfg)
    for key in ("pid", "lstart", "phase", "reason", "active", "sleep_prevented", "power", "children"):
        assert key in state, f"desktop/AXIOM.m reads {key}"
