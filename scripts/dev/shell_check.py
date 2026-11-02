#!/usr/bin/env python3
"""The window's own words, proved in the window: desktop/AXIOM.m compiled into a small bundle whose Python is a stand-in.

    uv run python scripts/dev/shell_check.py [--work dir] [--keep] [--full-disk]

No PostgreSQL, no Chrome, no engine: `boot` is a fake supervisor that writes the two files the window watches and obeys the same signals, and
the API is a fake that records POST /api/stop. HOME is a new empty folder and the bundle has its own identifier, so nothing here touches an
installed AXIOM, which this never starts. Dialogs are really shown and answered through AXIOM_SHELL_DRIVE; AXIOM_SHELL_BACKGROUND keeps the
keyboard where it is. --full-disk mounts a very small disk image as HOME and fills it, to see the window on a really full disk.

Exit status 0 only when every check holds. Run it with a slot, one at a time: it opens windows on the screen for a few seconds.
"""

import argparse
import http.server
import json
import os
import plistlib
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
BUNDLE_ID = "app.axiom.shellcheck"
SNAPSHOTS = ""
KEY_FILES = (
    "python/lib/python3.12/importlib/__init__.py",
    "app/jobpilot/native/desktop.py",
    "postgres/bin/postgres",
    "tex/bin/universal-darwin/pdflatex",
    "app/some/other/file.py",
)

FAKE_SUPERVISOR = r"""
import json, os, signal, subprocess, sys, time
home = os.environ["HOME"]
scenario_path = f"{home}/fake/scenario.json"
scenario = json.load(open(scenario_path))
args = sys.argv[1:]
command = next((a for a in args if a in {"boot", "update-check", "diagnostics", "login-item"}), "")
if command == "update-check":
    print(scenario.get("update", ""), end="")
    sys.exit(0)
if command == "diagnostics":
    print("fake diagnostics")
    sys.exit(0)
if command != "boot":
    sys.exit(0)
if scenario.get("boot_exit") is not None:
    sys.exit(scenario["boot_exit"])
native = f"{home}/Library/Application Support/AXIOM/data/native"
os.makedirs(native, exist_ok=True)
def write(name, value):
    with open(f"{native}/.{name}", "w") as f:
        json.dump(value, f)
    os.replace(f"{native}/.{name}", f"{native}/{name}")
done = subprocess.run(["/bin/ps", "-o", "lstart=", "-p", str(os.getpid())], env={"LC_ALL": "C", "TZ": "UTC"}, capture_output=True, text=True)
state = {"pid": os.getpid(), "lstart": done.stdout.strip(), "phase": "running", "active": scenario.get("active", 0), "work": scenario.get("work", {}),
         "finishing": [], "power": scenario.get("power", {}), "reason": "",
         "children": {"app": {"pid": os.getpid(), "grace": 180, "state": "up"}}}
write("window.json", {"base_url": scenario["base_url"], "token": "synthetic-token", "chrome_source": "installed"})
write("state.json", state)
flags = {"term": False, "usr1": False}
signal.signal(signal.SIGTERM, lambda *_: flags.update(term=True))
signal.signal(signal.SIGUSR1, lambda *_: flags.update(usr1=True))
owner = int(args[-1])
while not flags["term"]:
    time.sleep(0.1)
    try:
        os.kill(owner, 0)
    except ProcessLookupError:
        sys.exit(0)
    state["power"] = json.load(open(scenario_path)).get("power", {})
    write("state.json", state)
state.update(phase="stopping", finishing=scenario.get("finishing", []))
write("state.json", state)
open(f"{home}/fake/term", "w").close()
deadline = time.monotonic() + (scenario.get("drain", 0))
while time.monotonic() < deadline and not flags["usr1"]:
    time.sleep(0.1)
if flags["usr1"]:
    open(f"{home}/fake/usr1", "w").close()
state.update(phase="stopped", finishing=[])
write("state.json", state)
"""


class Api(http.server.BaseHTTPRequestHandler):
    stops = []

    def log_message(self, *_):
        pass

    def reply(self, body=b"{}", kind="application/json", cookie=False):
        self.send_response(200)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        if cookie:
            self.send_header("Set-Cookie", "session=synthetic; Path=/; HttpOnly")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self.reply(b"<!doctype html><title>Fake dashboard</title><h1 id=x>Fake dashboard</h1>", "text/html")

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        if self.path == "/api/stop":
            Api.stops.append(self.headers.get("Authorization"))
        self.reply(cookie=self.path == "/api/login")


class Window:
    def __init__(self, bundle, home, scenario, snapshots=None):
        self.bundle, self.home, self.scenario = bundle, Path(home), scenario
        self.drive = Path(tempfile.mkdtemp(prefix="drive-", dir=bundle.parent))
        (self.home / "fake").mkdir(parents=True, exist_ok=True)
        for name in ("term", "usr1"):
            (self.home / "fake" / name).unlink(missing_ok=True)
        self.save_scenario()
        env = {"HOME": str(home), "PATH": "/usr/bin:/bin", "LANG": "en_US.UTF-8", "AXIOM_SHELL_DRIVE": str(self.drive), "AXIOM_SHELL_BACKGROUND": "1"}
        with open(bundle.parent / "window-stderr.log", "ab") as stderr:
            self.process = subprocess.Popen([bundle / "Contents" / "MacOS" / "AXIOM"], env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                            stderr=stderr, start_new_session=True)
        self.ids = 0

    def save_scenario(self):
        path = self.home / "fake" / "scenario.json"
        try:
            path.write_text(json.dumps(self.scenario))
        except OSError:
            pass

    def op(self, timeout=20, **command):
        self.ids += 1
        command["id"] = self.ids
        temporary = self.drive / ".next"
        temporary.write_text(json.dumps(command))
        temporary.replace(self.drive / "next.json")
        result = self.drive / f"result-{self.ids}.json"
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if result.exists():
                return json.loads(result.read_text())
            if self.process.poll() is not None:
                return {"value": None, "error": f"the window ended with {self.process.returncode}"}
            time.sleep(0.05)
        return {"value": None, "error": "no answer"}

    def text(self):
        answer = self.op(body="var a=[...document.querySelectorAll('#acts a')].map(e=>e.textContent);"
                              "var t=document.getElementById('t');return {t:t?t.textContent:'',m:(document.getElementById('m')||{}).textContent||'',a:a,"
                              "h:(document.getElementById('x')||{}).textContent||''};", op="js")
        return answer.get("value") or {}

    def wait(self, until, seconds=25):
        deadline = time.monotonic() + seconds
        last = {}
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                return last
            last = self.text()
            if until(last):
                return last
            time.sleep(0.25)
        return last

    def dialogs(self):
        path = self.drive / "dialog.log"
        return path.read_text().splitlines() if path.exists() else []

    def wait_dialog(self, seconds=15):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if self.dialogs():
                return self.dialogs()
            time.sleep(0.1)
        return []

    def log(self):
        path = self.home / "Library" / "Logs" / "AXIOM" / "window.log"
        return path.read_text() if path.exists() else ""

    def ended(self, seconds=20):
        try:
            self.process.wait(timeout=seconds)
            return True
        except subprocess.TimeoutExpired:
            return False

    def shot(self, name):
        if SNAPSHOTS:
            self.op(op="snapshot", path=str(Path(SNAPSHOTS) / f"{name}.png"))

    def close(self):
        if self.process.poll() is None:
            self.process.kill()
            self.process.wait()
        for line in subprocess.run(["pgrep", "-f", str(self.home)], capture_output=True, text=True).stdout.split():
            try:
                os.kill(int(line), signal.SIGKILL)
            except (OSError, ValueError):
                pass


class Check:
    def __init__(self):
        self.failures = []

    def __call__(self, ok, text):
        print(f"  {'ok  ' if ok else 'FAIL'}  {text}", flush=True)
        if not ok:
            self.failures.append(text)


def build(work, source=None):
    """The bundle: the real window, Info.plist with its own identifier, and a stand-in for everything under Resources."""
    from jobpilot.native.desktop import write_install_manifest

    bundle = work / "AXIOM-QA.app"
    resources = bundle / "Contents" / "Resources"
    (bundle / "Contents" / "MacOS").mkdir(parents=True)
    subprocess.run(["clang", "-O1", "-fobjc-arc", "-Wall", "-Werror", "-mmacosx-version-min=13.0", "-arch", "arm64", "-framework", "Cocoa", "-framework", "WebKit",
                    str(source or ROOT / "desktop" / "AXIOM.m"), "-o", str(bundle / "Contents" / "MacOS" / "AXIOM")], check=True)
    (bundle / "Contents" / "Info.plist").write_bytes(plistlib.dumps({"CFBundleIdentifier": BUNDLE_ID, "CFBundleName": "AXIOM-QA", "CFBundleExecutable": "AXIOM",
                                                                     "CFBundleShortVersionString": "1.0.0", "CFBundleVersion": "shellcheck", "CFBundlePackageType": "APPL"}))
    for relative in KEY_FILES:
        path = resources / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("synthetic\n")
    (work / "fake_supervisor.py").write_text(FAKE_SUPERVISOR)
    python = resources / "python" / "bin" / "python3"
    python.parent.mkdir(parents=True)
    python.write_text(f'#!/bin/sh\nexec "{sys.executable}" -I "{work}/fake_supervisor.py" "$@"\n')
    python.chmod(0o755)
    write_install_manifest(resources)
    return bundle


def quit_scenarios(check, bundle, work, base):
    print("Quit with applications running: Stop and Quit, then Quit now")
    home = work / "home-a"
    window = Window(bundle, home, {
        "base_url": base, "active": 2, "work": {"running": 1, "submitting": 1, "queued": 3, "stopped": False}, "drain": 60,
        "finishing": [{"company": "Acme Example", "seconds_left": 120}],
    })
    try:
        seen = window.wait(lambda t: t.get("h") == "Fake dashboard")
        check(seen.get("h") == "Fake dashboard", "the window signs in and shows what the app serves")
        window.op(op="menu", title="Quit AXIOM")
        said = window.wait_dialog()
        check(bool(said) and said[0].startswith("quit: 2 applying now, 3 queued | "), f"the question counts what runs and waits: {said[0].split(' | ')[0] if said else 'no dialog'}")
        body = said[0] if said else ""
        check("stay stopped until you press Resume" in body and "carry on by themselves the next time you open it" in body and "nothing is sent twice" in body,
              "it says what each choice does")
        check(body.endswith("| Stop and Quit, Quit, Keep Running"), "its buttons are Stop and Quit, Quit, Keep Running")
        seen = window.wait(lambda t: t.get("t") == "Finishing up" and "Quit now" in (t.get("a") or []))
        check(Api.stops == ["Bearer synthetic-token"], "Stop and Quit recorded the stop first, under the token")
        check("Waiting for 1 application in the final submit step (Acme Example), up to 2:00 more" in seen.get("m", ""), "the drain names what it waits for")
        check("never sends it twice" in seen.get("m", ""), "and says cutting it off is not sending it twice")
        window.shot("finishing-up")
        window.op(op="js", body="document.querySelector('#acts a').click();return 1;")
        check(window.ended(), "Quit now ends it, and the window closes itself")
        check((home / "fake" / "usr1").exists(), "by SIGUSR1 to the supervisor, as docs/DESKTOP.md says")
        check("Quit: everything has stopped." in window.log(), "the log says everything stopped")
    finally:
        window.close()

    print("Quit while stopped: the Stop stays")
    Api.stops.clear()
    home = work / "home-b"
    window = Window(bundle, home, {"base_url": base, "active": 1, "work": {"running": 0, "submitting": 1, "queued": 4, "stopped": True}, "drain": 0})
    try:
        window.wait(lambda t: t.get("h") == "Fake dashboard")
        window.op(op="menu", title="Quit AXIOM")
        said = window.wait_dialog()
        body = said[0] if said else ""
        check(body.startswith("quit: 1 application is still ending | AXIOM is stopped and stays stopped after you quit."), "it says one is ending and the stop holds")
        check(body.endswith("| Quit, Keep Open"), "its buttons are Quit, Keep Open")
        check(window.ended(), "Quit closes it")
        check(Api.stops == [], "and asks for no second stop")
    finally:
        window.close()

    print("Quit with nothing running: no question")
    home = work / "home-c"
    window = Window(bundle, home, {"base_url": base, "active": 0})
    try:
        window.wait(lambda t: t.get("h") == "Fake dashboard")
        window.op(op="menu", title="Quit AXIOM")
        check(window.ended(), "it closes")
        check(not window.dialogs(), "without asking")
    finally:
        window.close()

    print("Stop and Quit when the app does not answer")
    home = work / "home-d"
    window = Window(bundle, home, {"base_url": "http://127.0.0.1:9", "active": 1, "work": {"running": 1, "submitting": 0, "queued": 0, "stopped": False}})
    try:
        time.sleep(3)
        window.op(op="menu", title="Quit AXIOM", timeout=5)
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline and len(window.dialogs()) < 2:
            time.sleep(0.2)
        said = window.dialogs()
        check(len(said) == 2 and said[1].startswith("quit-failed: AXIOM could not record the stop | ") and said[1].endswith("| Quit Anyway, Keep Running"),
              "it says the stop was not recorded, and asks again")
        check(window.ended(), "Quit Anyway closes it")
    finally:
        window.close()


def battery(check, bundle, work, base):
    print("Battery: one warning for each time the charger comes out")
    home = work / "home-e"
    window = Window(bundle, home, {"base_url": base, "active": 1, "work": {"running": 1, "submitting": 0, "queued": 0, "stopped": False}, "power": {"battery": False, "percent": 90}})
    try:
        window.wait(lambda t: t.get("h") == "Fake dashboard")
        check("Warned: applying on battery." not in window.log(), "plugged in: no warning")
        window.scenario["power"] = {"battery": True, "percent": 32}
        window.save_scenario()
        time.sleep(3.5)
        check(window.log().count("Warned: applying on battery.") == 1, "on battery: the warning shows")
        menu = window.op(op="status").get("value") or []
        check(any("On battery (32%). Plug in before leaving it applying" == line for line in menu), "and the Status menu says so")
        time.sleep(2.5)
        check(window.log().count("Warned: applying on battery.") == 1, "once, while it stays on battery")
        window.scenario["power"] = {"battery": False, "percent": 32}
        window.save_scenario()
        time.sleep(2.5)
        window.scenario["power"] = {"battery": True, "percent": 31}
        window.save_scenario()
        time.sleep(3.5)
        check(window.log().count("Warned: applying on battery.") == 2, "and again when the charger comes out again")
    finally:
        window.close()


def update_dialogs(check, bundle, work, base):
    print("Help, Check for Updates")
    home = work / "home-f"
    window = Window(bundle, home, {"base_url": base, "active": 0, "update": json.dumps({"current": "1.0.0", "latest": "", "url": "", "newer": False, "error": ""})})
    try:
        window.wait(lambda t: t.get("h") == "Fake dashboard")
        for update, words, what in (
            (json.dumps({"current": "1.0.0", "latest": "", "url": "", "newer": False, "error": ""}), "update: No update to offer | You have version 1.0.0.", "a 404 (private, or nothing published) never claims it is the newest"),
            (json.dumps({"current": "1.0.0", "latest": "1.0.0", "url": "https://github.com/x", "newer": False, "error": ""}), "update: AXIOM is up to date | You have version 1.0.0, the newest.", "GitHub's own number does"),
            (json.dumps({"current": "1.0.0", "latest": "", "url": "", "newer": False, "error": "Could not reach github.com (URLError)."}), "update: Could not check for updates | Could not reach github.com", "no network says so"),
            ("", "update: Could not check for updates | The check did not finish.", "a check that printed nothing says so"),
        ):
            window.scenario["update"] = update
            window.save_scenario()
            before = len(window.dialogs())
            window.op(op="menu", title="Check for Updates…")
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline and len(window.dialogs()) <= before:
                time.sleep(0.1)
            said = window.dialogs()[before:] if len(window.dialogs()) > before else [""]
            check(said[0].startswith(words), what)
            time.sleep(1)
    finally:
        window.close()


def incomplete(check, bundle, work, base):
    print("A copy that stopped part way")
    resources = bundle / "Contents" / "Resources"
    from jobpilot.native.desktop import install_problem

    check(install_problem(resources) == "", "the whole bundle passes")
    for what, undo, damage in (
        ("a file gone", None, lambda: (resources / "app/some/other/file.py").unlink()),
        ("a file cut short", None, lambda: (resources / "app/some/other/file.py").write_text("x")),
        ("Python's own files gone", None, lambda: (resources / "python/lib/python3.12/importlib/__init__.py").unlink()),
        ("the manifest gone", None, lambda: (resources / "install-manifest.txt").unlink()),
    ):
        keep = work / "keep"
        shutil.copytree(resources, keep, symlinks=True)
        damage()
        home = work / "home-g"
        shutil.rmtree(home, ignore_errors=True)
        window = Window(bundle, home, {"base_url": base, "active": 0})
        try:
            seen = window.wait(lambda t: t.get("t") == "AXIOM was not copied completely", seconds=15)
            check(seen.get("t") == "AXIOM was not copied completely", f"{what}: the window says so")
            check("drag it from the AXIOM disk image to Applications again, using Finder" in seen.get("m", ""), f"{what}: and says what to do")
            check(seen.get("a") == ["Show in Finder", "Copy diagnostics"], f"{what}: with its two links")
            window.shot("incomplete-copy")
            check("The installed copy is incomplete" in window.log(), f"{what}: the log has it")
            check(not (home / "Library" / "Application Support" / "AXIOM" / "data" / "native" / "state.json").exists(), f"{what}: nothing was started")
            window.op(op="menu", title="Quit AXIOM")
            check(window.ended(10), f"{what}: Quit still works")
        finally:
            window.close()
            shutil.rmtree(resources)
            keep.rename(resources)


def full_disk(check, bundle, work, base):
    print("A really full disk")
    image, mount = work / "tiny.dmg", work / "tiny"
    mount.mkdir(exist_ok=True)
    subprocess.run(["hdiutil", "create", "-quiet", "-size", "8m", "-fs", "HFS+", "-volname", "qatiny", str(image)], check=True)
    subprocess.run(["hdiutil", "attach", "-quiet", "-nobrowse", "-noverify", "-mountpoint", str(mount), str(image)], check=True)
    try:
        home = mount / "home"
        (home / "fake").mkdir(parents=True)
        (home / "fake" / "scenario.json").write_text(json.dumps({"base_url": base, "boot_exit": 1}))
        (home / "Library" / "Logs" / "AXIOM").mkdir(parents=True)
        (home / "Library" / "Logs" / "AXIOM" / "window.log").touch()
        with open(mount / "filler", "wb") as filler:
            try:
                while True:
                    filler.write(b"\0" * 1024)
                    filler.flush()
            except OSError:
                pass
        check(shutil.disk_usage(mount).free < 1024 * 1024, "the volume is full")
        window = Window(bundle, home, {"base_url": base, "boot_exit": 1})
        try:
            seen = window.wait(lambda t: t.get("t") == "This Mac's disk is full", seconds=20)
            check(window.process.poll() is None, "the window is still alive: writing its log did not end it")
            check(seen.get("t") == "This Mac's disk is full", "it says the disk is full")
            check("Free some space" in seen.get("m", ""), "and what to do")
            check(seen.get("a") == ["Try again", "Copy diagnostics", "Open logs"], "with a way to try again")
            window.shot("disk-full")
            window.op(op="menu", title="Quit AXIOM")
            check(window.ended(10), "and Quit works")
        finally:
            window.close()
    finally:
        subprocess.run(["hdiutil", "detach", "-quiet", "-force", str(mount)], check=False)


def clean_library():
    """What macOS keeps under the real home for this identifier, whatever HOME says."""
    home = Path(os.path.expanduser("~"))
    for relative in (f"Library/WebKit/{BUNDLE_ID}", f"Library/HTTPStorages/{BUNDLE_ID}", f"Library/Caches/{BUNDLE_ID}", f"Library/Preferences/{BUNDLE_ID}.plist",
                     f"Library/Saved Application State/{BUNDLE_ID}.savedState", f"Library/HTTPStorages/{BUNDLE_ID}.binarycookies"):
        path = home / relative
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        else:
            path.unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--work", help="Where the throwaway bundle and homes go")
    parser.add_argument("--keep", action="store_true")
    parser.add_argument("--full-disk", action="store_true")
    parser.add_argument("--only-full-disk", action="store_true")
    parser.add_argument("--source", help="Build the window from this file, to see what another version does")
    parser.add_argument("--snapshots", help="A folder for pictures of what the window showed")
    options = parser.parse_args(argv)
    global SNAPSHOTS
    SNAPSHOTS = options.snapshots or ""
    work = Path(os.path.realpath(tempfile.mkdtemp(prefix="axs-", dir=options.work)))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Api)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    check = Check()
    try:
        bundle = build(work, options.source)
        if not options.only_full_disk:
            quit_scenarios(check, bundle, work, base)
            battery(check, bundle, work, base)
            update_dialogs(check, bundle, work, base)
            incomplete(check, bundle, work, base)
        if options.full_disk or options.only_full_disk:
            full_disk(check, bundle, work, base)
    finally:
        server.shutdown()
        clean_library()
        if not options.keep:
            shutil.rmtree(work, ignore_errors=True)
    print("PASSED" if not check.failures else f"FAILED: {len(check.failures)}")
    return 1 if check.failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
