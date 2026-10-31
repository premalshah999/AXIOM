"""What the native app reads and what it hands the app: `.env` stays the one source of config, as under Compose."""

import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PG_PORT = 54329
LOCAL_HOSTS = {"127.0.0.1", "localhost"}
SYSTEM_CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
# A daemon started outside a login shell still has to find pdflatex (resume tailoring), brew's tools and node.
EXTRA_PATH = ("/opt/homebrew/bin", "/usr/local/bin", "/Library/TeX/texbin")


class NativeError(RuntimeError):
    """A problem the owner can act on. The message says what to do; no traceback."""


def read_dotenv(path):
    """KEY=value lines as python-dotenv (which the app uses) reads the ones `.env.example` and setup.sh write."""
    values = {}
    try:
        text = Path(path).read_text()
    except FileNotFoundError:
        return values
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, sep, value = line.partition("=")
        if not sep:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        values[key.strip()] = value
    return values


def local_cdp(url):
    """The Chrome debugging address as this process reaches it. A `.env` written for Docker says host.docker.internal,
    which means this same computer and does not resolve outside Docker; anything else non-local is refused, because a
    debugging port is full control of the browser."""
    url = (url or "").strip().rstrip("/")
    if not url:
        return ""
    parts = urlsplit(url)
    if parts.hostname == "host.docker.internal":
        return urlunsplit((parts.scheme, f"127.0.0.1:{parts.port or 80}", "", "", ""))
    if parts.scheme != "http" or parts.hostname not in LOCAL_HOSTS or not parts.port:
        raise NativeError(f"The Chrome debugging address must be local http with a port, got: {url}")
    return url


def system_chrome():
    return SYSTEM_CHROME if os.access(SYSTEM_CHROME, os.X_OK) else shutil.which("google-chrome") or ""


@dataclass(frozen=True)
class Native:
    root: Path
    values: dict
    data_dir: Path
    app_port: int
    base_url: str
    pg_port: int
    password: str
    worker_cdp: str
    twin_cdp: str
    drain_seconds: int

    @property
    def home(self):
        return self.data_dir / "native"

    @property
    def pgdata(self):
        return self.data_dir / "postgres"

    @property
    def logs(self):
        return self.home / "logs"

    @property
    def state_file(self):
        return self.home / "state.json"

    @property
    def releases(self):
        return self.home / "releases"

    @property
    def current(self):
        return self.home / "current"

    @property
    def migration(self):
        return self.data_dir / "migration"

    @property
    def database_url(self):
        return f"postgresql+psycopg://applypilot:{quote(self.password, safe='')}@127.0.0.1:{self.pg_port}/applypilot"

    @property
    def stop_grace(self):
        # The same margin as compose.yaml: SHUTDOWN_DRAIN_SECONDS (150) + 30 = stop_grace_period 180 s, because a kill
        # after this would turn a commit still running into an unknown submission.
        return self.drain_seconds + 30

    def get(self, key, default=""):
        return self.values.get(key, default)

    def path_with_tools(self, environ=None):
        current = (environ if environ is not None else os.environ).get("PATH", "/usr/bin:/bin").split(os.pathsep)
        return os.pathsep.join(dict.fromkeys([*current, *EXTRA_PATH]))

    def app_environment(self, release):
        """The environment of `jobpilot serve`. These are what compose.yaml's `environment:` block and the Dockerfile's
        ENV set, and for the same reason they outrank `.env`; everything else the app reads from `.env` itself."""
        env = dict(os.environ)
        env.update(
            DATA_DIR=str(self.data_dir),
            APP_PORT=str(self.app_port),
            DATABASE_URL=self.database_url,
            DBOS_DATABASE_URL=self.database_url,
            BASE_URL=self.base_url,
            BROWSER_CDP_URL=self.worker_cdp,
            BROWSER_TWIN_CDP_URL=self.twin_cdp,
            # Without a worker Chrome the app launches a headless browser: the installed Chrome, so that no separate
            # 300 MB Chromium has to be downloaded (the image's Chromium exists only because the image has no Chrome).
            CHROMIUM_PATH=self.get("CHROMIUM_PATH") or system_chrome(),
            PYTHONUNBUFFERED="1",
            PYTHONUTF8="1",
            # The container's clock zone is UTC, and date.today() and PostgreSQL's now()::text rely on it; TIMEZONE in
            # `.env` remains what decides the daily limits and the dates written on forms. The Mac's own zone here would
            # shift every naive "today" for part of each day.
            TZ="UTC",
            PATH=self.path_with_tools(env),
            # The code that runs is the release, not the checkout (release.py). PYTHONSAFEPATH keeps the current
            # directory (the checkout, where `.env` is) off sys.path, here and in the child processes the app starts.
            PYTHONPATH=str(release),
            PYTHONSAFEPATH="1",
        )
        return env


def load(root=None, environ=None):
    root = Path(root or ROOT)
    environ = os.environ if environ is None else environ
    values = {**read_dotenv(root / ".env"), **environ}  # The environment outranks `.env`, as in pydantic-settings.
    data_dir = Path(values.get("DATA_DIR") or ".data").expanduser()
    data_dir = data_dir if data_dir.is_absolute() else root / data_dir
    app_port = _port(values, "APP_PORT", 8080)
    return Native(
        root=root,
        values=values,
        data_dir=data_dir,
        app_port=app_port,
        base_url=(values.get("BASE_URL") or f"http://127.0.0.1:{app_port}").rstrip("/"),
        pg_port=_port(values, "AXIOM_PG_PORT", DEFAULT_PG_PORT),
        password=values.get("POSTGRES_PASSWORD", ""),
        worker_cdp=local_cdp(values.get("BROWSER_CDP_URL", "")),
        twin_cdp=local_cdp(values.get("BROWSER_TWIN_CDP_URL", "")),
        drain_seconds=_port(values, "SHUTDOWN_DRAIN_SECONDS", 150),
    )


def _port(values, key, default):
    raw = values.get(key) or default
    try:
        return int(raw)
    except (TypeError, ValueError):
        raise NativeError(f"{key} must be a number, got '{raw}'") from None


def prepare(cfg):
    """The one thing `.env` must supply, and the permissions the data must have. The data dir stays 0700, as the
    container's is, because it holds the mail key and every saved page of every application."""
    if not cfg.password:
        raise NativeError("POSTGRES_PASSWORD is not set in .env. Create one with: scripts/setup.sh --env-only")
    cfg.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    cfg.data_dir.chmod(0o700)
    # Spotlight would otherwise read hundreds of thousands of saved HTML pages into its own index.
    (cfg.data_dir / ".metadata_never_index").touch()
    for folder in (cfg.home, cfg.logs, cfg.releases):
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    env_file = cfg.root / ".env"
    if env_file.exists() and env_file.stat().st_mode & 0o077:
        env_file.chmod(0o600)
    return cfg
