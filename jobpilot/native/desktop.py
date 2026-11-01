"""AXIOM as one installed Mac app: what `scripts/axiom` gets from a checkout, worked out for a bundle (docs/DESKTOP.md).

The supervisor is not forked. This module answers what a checkout answers by being one: where the config and the data
live (never in the bundle, which an upgrade replaces and a signature seals), which ports are free and which Chrome to use.
Then it becomes the supervisor with exec, so that the window's child is the supervisor itself and nothing extra stays
resident. It only uses the standard library, because it runs before anything else has been checked.

`python -m jobpilot.native.desktop boot` is what the window runs. Everything else (`status`, `jobpilot verify`, `logs`,
`psql`, `hold on`, `move-in --from <checkout>`) is `scripts/axiom` with this environment on top.
"""

import fcntl
import json
import os
import platform
import plistlib
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import traceback
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from . import release
from .env import ROOT, NativeError, load, prepare
from .supervisor import port_in_use, process_start, read_state, stop_chrome_holding, stop_leftovers, supervisor_alive

BUNDLE_ID = "app.axiom.desktop"
# What a checkout install uses by default, and what production holds on this Mac. An app beside a checkout never takes them.
AVOID_PORTS = frozenset({8080, 9224, 9225, 9339, 54329})
PORTS = ("app", "postgres", "worker", "twin")
# A Finder launch has only these. Homebrew is deliberately absent, here and in env.py: if the app needed it, the first clean
# Mac would be where we found out. pdflatex comes from the bundle (desktop/bundle_tex.py), ahead of any on the Mac.
TOOL_PATH = ("/usr/bin", "/bin", "/usr/sbin", "/sbin")
INSTALLED_CHROME = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "~/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
)
CHROME_INDEX = "https://googlechromelabs.github.io/chrome-for-testing/last-known-good-versions-with-downloads.json"
CHROME_HOSTS = {"storage.googleapis.com"}
CHROME_APP = "Google Chrome for Testing.app"
CHROME_NEEDS_BYTES = 1500 * 1024 * 1024  # The zip and what it unpacks to, both on disk for a moment.
# `scripts/axiom` commands that assume a checkout: a bundle has no source to snapshot, and no Homebrew to install from.
CHECKOUT_ONLY = {"setup", "start", "deploy", "migrate", "_supervise"}


@dataclass(frozen=True)
class Layout:
    home: Path
    resources: Path

    @classmethod
    def here(cls, environ=None):
        environ = os.environ if environ is None else environ
        # HOME, not the password database: Finder sets it, and it is what lets a test instance stay out of the real folder.
        home = Path(environ.get("HOME") or Path.home())
        # This file is Resources/app/jobpilot/native/desktop.py.
        return cls(home=home, resources=Path(environ.get("AXIOM_BUNDLE") or ROOT.parent))

    @property
    def support(self):
        return self.home / "Library" / "Application Support" / "AXIOM"

    @property
    def data(self):
        return self.support / "data"

    @property
    def logs(self):
        return self.home / "Library" / "Logs" / "AXIOM"

    @property
    def env_file(self):
        return self.support / ".env"

    @property
    def ports_file(self):
        return self.support / "ports.json"

    @property
    def native(self):
        return self.data / "native"

    @property
    def window_file(self):
        return self.native / "window.json"

    @property
    def progress_file(self):
        return self.native / "boot.json"

    @property
    def moved_from_file(self):
        return self.data / "migration" / "moved-from.json"

    @property
    def app(self):
        return self.resources / "app"

    @property
    def pg_bin(self):
        return self.resources / "postgres" / "bin"

    @property
    def tex_bin(self):
        return self.resources / "tex" / "bin" / "universal-darwin"

    @property
    def bundle(self):
        return self.resources.parent.parent

    @property
    def downloaded_chrome(self):
        return self.support / "chrome" / CHROME_APP / "Contents" / "MacOS" / "Google Chrome for Testing"

    @property
    def login_item(self):
        return self.home / "Library" / "LaunchAgents" / f"{BUNDLE_ID}.plist"


def write_private(path, text):
    """Whole or not at all, and never readable by anyone else, not even for a moment: these hold secrets."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    with os.fdopen(fd, "w") as f:
        f.write(text)
    os.replace(temporary, path)


def read_json(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return {}


# ---- first run: config and secrets ----------------------------------------------------------------------------------


def detect_timezone():
    try:
        target = os.readlink("/etc/localtime")
    except OSError:
        return ""
    return target.split("zoneinfo/", 1)[1] if "zoneinfo/" in target else ""


def physical_memory_gb():
    return os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE") / 1024**3


def first_config(memory_gb=None):
    """What scripts/setup.sh writes on a fresh clone. There is nothing else to configure: keys go in the app."""
    lines = [
        f"# Written by AXIOM on first run ({time.strftime('%Y-%m-%d')}). Your API keys go in the app, not here.",
        f"POSTGRES_PASSWORD={secrets.token_hex(24)}",
    ]
    if zone := detect_timezone():
        lines.append(f"TIMEZONE={zone}")
    # Each worker drives its own Chrome tab: a 16 GB Mac ran out of memory with eight (2026-10-26).
    if (physical_memory_gb() if memory_gb is None else memory_gb) <= 16:
        lines += ["NORMAL_WORKERS=3", "SPECIALIST_WORKERS=1", "DISCOVERY_WORKERS=1"]
    return "\n".join(lines) + "\n"


def ensure_config(layout):
    """Created once, and never replaced: this password is what the database was created with."""
    layout.support.mkdir(parents=True, exist_ok=True, mode=0o700)
    layout.support.chmod(0o700)
    created = not layout.env_file.exists()
    if created:
        write_private(layout.env_file, first_config())
    return created


# ---- ports ----------------------------------------------------------------------------------------------------------


def port_free(port):
    if port in AVOID_PORTS or port_in_use(port):
        return False
    with socket.socket() as s:
        # Without SO_REUSEADDR a restart's TIME_WAIT would read as taken, and the window's address would change for nothing.
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def new_port(taken):
    while True:
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        if port not in taken and port not in AVOID_PORTS:
            return port


def pick_ports(layout):
    """Free loopback ports, so that two copies cannot collide and a test can run beside production. They stay the same from
    one start to the next when they can, because the page's preferences belong to its address."""
    saved = read_json(layout.ports_file)
    chosen = {}
    for name in PORTS:
        port = saved.get(name)
        taken = set(chosen.values())
        valid = isinstance(port, int) and 1024 <= port < 65536 and port not in taken and port_free(port)
        chosen[name] = port if valid else new_port(taken)
    if chosen != saved:
        write_private(layout.ports_file, json.dumps(chosen))
    return chosen


# ---- Chrome ---------------------------------------------------------------------------------------------------------


def find_chrome(layout, installed=INSTALLED_CHROME):
    """(path, where it came from), or ('', ''). The person's own Chrome first: it is what they already keep up to date."""
    for candidate in installed:
        path = candidate.replace("~", str(layout.home), 1)
        if os.access(path, os.X_OK):
            return path, "installed"
    if os.access(layout.downloaded_chrome, os.X_OK):
        return str(layout.downloaded_chrome), "downloaded"
    return "", ""


def chrome_platform():
    return "mac-arm64" if platform.machine() == "arm64" else "mac-x64"


def _allowed(url, hosts):
    parts = urllib.parse.urlsplit(url)
    return (parts.scheme == "https" and parts.hostname in hosts) or (
        # A local mirror, and the tests.
        parts.scheme == "http"
        and parts.hostname == "127.0.0.1"
    )


def download_chrome(layout, report, *, index=None):
    """Chrome for Testing, on a Mac with no Chrome. Nothing is left half-unpacked: it appears in one rename, or not at all."""
    target = layout.support / "chrome" / CHROME_APP
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    for stale in target.parent.glob(".download-*"):  # A Quit during the last download.
        shutil.rmtree(stale, ignore_errors=True)
    free = shutil.disk_usage(target.parent).free
    if free < CHROME_NEEDS_BYTES:
        raise NativeError(
            f"AXIOM needs Google Chrome, and downloading it takes about 1.5 GB of free disk. {free / 1024**3:.1f} GB is free. "
            "Free some space or install Google Chrome, then try again."
        )
    report("chrome", "Finding the latest Chrome ...", None)
    try:
        with urllib.request.urlopen(index or os.environ.get("AXIOM_CHROME_INDEX") or CHROME_INDEX, timeout=30) as response:
            stable = json.load(response)["channels"]["Stable"]
        wanted = chrome_platform()
        url = next(d["url"] for d in stable["downloads"]["chrome"] if d["platform"] == wanted)
        if not _allowed(url, CHROME_HOSTS):
            raise ValueError(f"unexpected download address {url}")
        with tempfile.TemporaryDirectory(dir=target.parent, prefix=".download-") as work:
            archive = Path(work) / "chrome.zip"
            with urllib.request.urlopen(url, timeout=60) as response, open(archive, "wb") as out:
                total = int(response.headers.get("Content-Length") or 0)
                done, shown = 0, -1
                while chunk := response.read(1024 * 1024):
                    out.write(chunk)
                    done += len(chunk)
                    percent = int(done * 100 / total) if total else None
                    if percent != shown:
                        shown = percent
                        size = f"{done / 1024**2:.0f} of {total / 1024**2:.0f} MB" if total else f"{done / 1024**2:.0f} MB"
                        report("chrome", f"Downloading Chrome, which AXIOM applies through ({size})", percent)
            report("chrome", "Unpacking Chrome ...", None)
            # unzip, because it keeps the framework's symlinks and the program's execute bit. `ditto -x -k` dropped the bit.
            subprocess.run(["/usr/bin/unzip", "-q", str(archive), "-d", work], check=True, capture_output=True, timeout=600)
            found = next(Path(work).glob(f"*/{CHROME_APP}"), None)
            if not found:
                raise ValueError("the download did not contain Chrome")
            if target.exists():
                shutil.rmtree(target)
            found.rename(target)
    except (OSError, ValueError, KeyError, StopIteration, subprocess.SubprocessError) as exc:
        raise NativeError(
            "AXIOM needs Google Chrome and could not download it. Check your connection, or install Google Chrome from "
            f"google.com/chrome, then try again. ({type(exc).__name__}: {str(exc)[:160]})"
        ) from None
    if not os.access(layout.downloaded_chrome, os.X_OK):
        raise NativeError("The Chrome download did not contain a program AXIOM can run. Install Google Chrome, then try again.")
    return str(layout.downloaded_chrome)


# ---- what the app runs with -----------------------------------------------------------------------------------------


def environment(layout, ports=None, chrome=""):
    """What makes jobpilot.native run from a bundle. All of it outranks `.env`, as environment does everywhere here."""
    env = {
        "AXIOM_BUNDLE": str(layout.resources),
        "AXIOM_CONFIG_DIR": str(layout.support),
        "AXIOM_LOG_DIR": str(layout.logs),
        "AXIOM_PG_BIN": str(layout.pg_bin),
        "DATA_DIR": str(layout.data),
        "PATH": os.pathsep.join([str(layout.tex_bin), *TOOL_PATH]),
        # A write inside the bundle would break its signature; the build compiles everything ahead of time.
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
        # Otherwise the working directory comes first on sys.path. Started from a checkout's folder, this imported that
        # checkout's `jobpilot` instead of its own (2026-10-31, on the first launch beside production).
        "PYTHONSAFEPATH": "1",
        # The bundled OpenSSLs were built to read /opt/homebrew/etc/openssl@3/openssl.cnf. On a Mac with Homebrew that would be
        # someone else's config inside the app's TLS; on one without, a failed read at every start (the clean-run trace, 2026-10-31).
        "OPENSSL_CONF": str(layout.resources / "openssl.cnf"),
    }
    if ports:
        env.update(
            APP_PORT=str(ports["app"]),
            AXIOM_PG_PORT=str(ports["postgres"]),
            BASE_URL=f"http://127.0.0.1:{ports['app']}",
            BROWSER_CDP_URL=f"http://127.0.0.1:{ports['worker']}",
            BROWSER_TWIN_CDP_URL=f"http://127.0.0.1:{ports['twin']}",
        )
    if chrome:
        env.update(CHROME_BIN=chrome, CHROMIUM_PATH=chrome)
    return env


def access_token(cfg):
    """The window signs itself in with this, so that no one meets a sign-in wall on their own Mac. The API still checks it.
    The same file Settings.prepare reads and creates; `.env`'s APP_TOKEN wins there too, for a moved-in installation."""
    if cfg.get("APP_TOKEN"):
        return cfg.get("APP_TOKEN")
    path = cfg.data_dir / "access-token"
    if not path.exists():
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(secrets.token_urlsafe(32))
    return path.read_text().strip()


def set_login_item(layout, on):
    """A LaunchAgent, which needs no signature, works from any folder and can be turned off by deleting one file."""
    if not on:
        layout.login_item.unlink(missing_ok=True)
        return
    layout.login_item.parent.mkdir(parents=True, exist_ok=True)
    layout.login_item.write_bytes(
        plistlib.dumps(
            {
                "Label": BUNDLE_ID,
                "ProgramArguments": ["/usr/bin/open", "-a", str(layout.bundle)],
                "RunAtLoad": True,
                "LimitLoadToSessionType": "Aqua",
            }
        )
    )


def refresh_login_item(layout):
    """The app was moved since this was turned on: at the next login it should open from where it is now."""
    if not layout.login_item.exists():
        return
    try:
        arguments = plistlib.loads(layout.login_item.read_bytes()).get("ProgramArguments") or []
    except (OSError, ValueError, plistlib.InvalidFileException):
        arguments = []
    if str(layout.bundle) not in arguments:
        set_login_item(layout, True)


def old_installation_running(layout):
    """A moved-in checkout started again beside the app: two databases, each thinking it alone applies to these jobs, would
    send every application twice. So the app will not start until the old one has stopped.

    Asked of the process table, not the old folder: a checkout on the Desktop or in Documents sits behind a macOS privacy
    prompt, and reading its .env froze the first start until someone answered (2026-10-31). What applies is its database and
    its Chrome, and each names its folder in its arguments."""
    source = read_json(layout.moved_from_file).get("source")
    if not source:
        return ""
    try:
        table = subprocess.run(["ps", "-axww", "-o", "command="], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""
    holding = re.compile(r"(?:\s-D\s+|--user-data-dir=)" + re.escape(str(source).rstrip("/")) + "/")
    return source if holding.search(table) else ""


# ---- boot -----------------------------------------------------------------------------------------------------------


def boot(layout, owner=None):
    """Everything between a double-click and the supervisor. The window shows `boot.json` while this runs."""
    layout.native.mkdir(parents=True, exist_ok=True, mode=0o700)
    layout.logs.mkdir(parents=True, exist_ok=True, mode=0o700)

    def report(step, message, percent=None):
        write_private(layout.progress_file, json.dumps({"step": step, "message": message, "percent": percent}))

    try:
        return _boot(layout, owner, report)
    except NativeError as exc:
        report("failed", str(exc))
        print(f"axiom: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # A bug here must reach the window as words, not leave it waiting.
        report("failed", f"AXIOM hit a problem while starting ({type(exc).__name__}). Your data is safe, and the logs say what.")
        traceback.print_exc()
        return 1


def _boot(layout, owner, report):
    report("preparing", "Getting things ready ...")
    # Held until the supervisor exits, across exec: a second copy of the app must neither pick other ports nor rewrite
    # the window's file under the first.
    lock = os.open(layout.native / "boot.lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return 0  # Already starting or running: the window just watches state.json.
    os.set_inheritable(lock, True)
    ensure_config(layout)
    refresh_login_item(layout)
    os.environ.update(environment(layout))
    cfg = prepare(load())
    state = read_state(cfg)
    if supervisor_alive(state):
        return 0
    if source := old_installation_running(layout):
        raise NativeError(
            f"Your earlier installation at {source} is still running. Two of them would apply to the same jobs twice. "
            "Stop it first (scripts/axiom stop), then try again."
        )
    # Before choosing ports: what a dead supervisor left running holds the ports this installation already owns.
    stop_leftovers(cfg, state, say=lambda line: print(line, flush=True))
    # A relaunched Chrome no state file knows of. A new one on this profile would only hand over to it and exit.
    for profile in ("desktop-browser", "twin-browser"):
        stop_chrome_holding(cfg.data_dir / profile, say=lambda line: print(line, flush=True))
    ports = pick_ports(layout)
    chrome, source = find_chrome(layout)
    if not chrome:
        chrome, source = download_chrome(layout, report), "downloaded"
    os.environ.update(environment(layout, ports, chrome))
    if owner:
        os.environ.update(AXIOM_OWNER_PID=str(owner), AXIOM_OWNER_LSTART=process_start(owner))
    cfg = prepare(load())
    release.activate(cfg, layout.app)
    write_private(
        layout.window_file,
        json.dumps(
            {
                "base_url": cfg.base_url,
                "token": access_token(cfg),
                "chrome": chrome,
                "chrome_source": source,
                "data": str(layout.data),
                "logs": str(layout.logs),
            }
        ),
    )
    report("starting", "Starting AXIOM ...")
    _detach(layout)
    os.execv(sys.executable, [sys.executable, "-u", "-m", "jobpilot.native", "_supervise"])


def _detach(layout):
    """Its own session, so that a signal to the window is not one to the database; its output in the log, because the window
    has nowhere to show it; and the config folder as its working directory, where the app reads `.env`."""
    os.chdir(layout.support)
    try:
        os.setsid()
    except PermissionError:
        pass  # Already a session leader.
    log = os.open(layout.logs / "supervisor.log", os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    null = os.open(os.devnull, os.O_RDONLY)
    os.dup2(null, 0)
    os.dup2(log, 1)
    os.dup2(log, 2)
    os.close(log)
    os.close(null)


# ---- the rest of `axiom`, with this environment ----------------------------------------------------------------------


def existing_environment(layout):
    """For a command beside a running app: the ports and Chrome it already chose, without choosing again."""
    ports = read_json(layout.ports_file)
    if not ports:
        raise NativeError("AXIOM has not been opened yet. Open it once first.")
    return environment(layout, ports, read_json(layout.window_file).get("chrome", ""))


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    layout = Layout.here()
    command = argv[0] if argv else "status"
    try:
        if command == "boot":
            return boot(layout, owner=int(argv[1]) if len(argv) > 1 else None)
        if command == "login-item":
            if argv[1:] in (["on"], ["off"]):
                set_login_item(layout, argv[1] == "on")
            print("on" if layout.login_item.exists() else "off")
            return 0
        if command == "move-in":
            from . import movein

            return movein.main(layout, argv[1:])
        if command in CHECKOUT_ONLY:
            print(f"axiom: `{command}` is for a checkout install. This app starts by opening it.", file=sys.stderr)
            return 2
        os.environ.update(existing_environment(layout))
    except NativeError as exc:
        print(f"axiom: {exc}", file=sys.stderr)
        return 1
    from .cli import main as axiom

    return axiom(argv or ["status"])


if __name__ == "__main__":
    raise SystemExit(main())
