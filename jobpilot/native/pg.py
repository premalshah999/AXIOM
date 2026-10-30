"""AXIOM's own PostgreSQL cluster: found on this computer, started by the supervisor, never a system-wide service.

The cluster is private to the installation (DATA_DIR/postgres, its own port, 127.0.0.1 only), so an existing Homebrew or
EDB server on 5432 is neither used nor disturbed.
"""

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from .env import NativeError

MAJOR = "17"  # compose.yaml's major version: a dump restores into it without an upgrade step.
USER = DATABASE = "applypilot"  # compose.yaml's names, which a restored dump expects.
SEARCH = (
    "/opt/homebrew/opt/postgresql@17/bin",
    "/usr/local/opt/postgresql@17/bin",
    "/Applications/Postgres.app/Contents/Versions/17/bin",
    "/Library/PostgreSQL/17/bin",
)
# Sized for the app's real pool: jobpilot.db (10 + 20 overflow) plus DBOS's system pool (20, no overflow, the default in
# dbos/_dbos_config.py) is 50; the rest is the supervisor's poll, `axiom psql` and pg_dump. Production holds about 30.
MAX_CONNECTIONS = 60
SHARED_BUFFERS = "64MB"
# Passed on the command line, not in a file, so that an update can never leave an old configuration on disk and `ps`
# shows what is in force.
SETTINGS = {
    "listen_addresses": "127.0.0.1",
    "unix_socket_directories": "",  # TCP only: one way in, password required, and no socket path to outgrow 103 bytes.
    "max_connections": MAX_CONNECTIONS,
    "shared_buffers": SHARED_BUFFERS,
    "huge_pages": "off",
    "work_mem": "4MB",
    "maintenance_work_mem": "32MB",
    "temp_buffers": "4MB",
    "wal_buffers": "4MB",
    "effective_cache_size": "1GB",
    "max_worker_processes": 2,
    "max_parallel_workers": 0,
    "max_parallel_workers_per_gather": 0,
    "max_parallel_maintenance_workers": 0,
    "autovacuum_max_workers": 2,
    "jit": "off",
    "max_wal_size": "512MB",  # Disk is the scarce thing here: this bounds WAL between checkpoints.
    "min_wal_size": "64MB",
    "checkpoint_timeout": "10min",
    "checkpoint_completion_target": 0.9,
    # macOS's fsync() returns before the drive has the data (pg_test_fsync on this Mac: 61 us, against 4.3 ms for a real
    # flush), and this Mac has lost power mid-batch (2026-10-19), so every flush here is a real one and the WAL's order
    # survives a power cut. Waiting for one at every commit would put 4 ms on each of about 60 event inserts a second, on
    # an event loop already known to starve on database time (2026-10-15), so a plain commit does not wait: at worst a
    # crash loses its last half second of those. Db.exclusive() turns the wait back on for its own transaction, so a
    # reservation that says "submitting" is on the drive before the click. Docker's flush was 89 us: never a real one.
    "wal_sync_method": "fsync_writethrough",
    "synchronous_commit": "off",
    "timezone": "UTC",  # The container's zone: the app's timestamps are stored as text in places (see env.py, TZ).
    "log_timezone": "UTC",
    "log_min_messages": "warning",
    "log_checkpoints": "off",
    "log_line_prefix": "%m [%p] ",
}


def find_bin(cfg):
    """The directory of a PostgreSQL 17 installation, or a message saying how to get one."""
    candidates = [cfg.get("AXIOM_PG_BIN"), *SEARCH]
    on_path = shutil.which("postgres")
    if on_path:
        candidates.append(str(Path(on_path).parent))
    for folder in filter(None, candidates):
        binary = Path(folder) / "postgres"
        if not os.access(binary, os.X_OK):
            continue
        out = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=10).stdout
        if f" {MAJOR}." in out:
            return Path(folder)
    raise NativeError(f"PostgreSQL {MAJOR} was not found. Install it with: brew install postgresql@{MAJOR}")


def server_env():
    """A valid locale is not optional here: with none, macOS starts a thread inside setlocale() and the postmaster refuses
    to run ("postmaster became multithreaded during startup"), which a daemon started from anywhere can meet."""
    return {**os.environ, "TZ": "UTC", "LC_ALL": "en_US.UTF-8"}


def server_argv(bin_dir, cfg, **overrides):
    settings = {**SETTINGS, "shared_buffers": cfg.get("AXIOM_PG_SHARED_BUFFERS") or SHARED_BUFFERS, **overrides}
    argv = [str(bin_dir / "postgres"), "-D", str(cfg.pgdata), "-p", str(cfg.pg_port)]
    for key, value in settings.items():
        argv += ["-c", f"{key}={value}"]
    return argv


def init_cluster(bin_dir, cfg):
    """Create the cluster once. Returns True when it was created. An existing one is never replaced, and never used
    if its major version is not the one this code expects."""
    version = cfg.pgdata / "PG_VERSION"
    if version.exists():
        found = version.read_text().strip()
        if found != MAJOR:
            raise NativeError(f"{cfg.pgdata} is PostgreSQL {found}, but AXIOM runs {MAJOR}. Upgrade it with pg_upgrade.")
        return False
    if cfg.pgdata.exists() and any(cfg.pgdata.iterdir()):
        raise NativeError(f"{cfg.pgdata} exists but is not a PostgreSQL cluster. Move it aside and start again.")
    # ICU en-US sorts text as the container's glibc en_US.utf8 does. macOS's own en_US.UTF-8 sorts by byte value, so
    # "Zeta" would come before "apple" in every ordered list; libc is the fallback for a build without ICU.
    locales = (["--locale-provider=icu", "--icu-locale=en-US", "--locale=en_US.UTF-8"], ["--locale=en_US.UTF-8"])
    with tempfile.NamedTemporaryFile("w", dir=cfg.home, prefix=".pw-") as secret:
        secret.write(cfg.password)
        secret.flush()
        for attempt, locale in enumerate(locales):
            done = subprocess.run(
                [
                    str(bin_dir / "initdb"),
                    "-D",
                    str(cfg.pgdata),
                    "-U",
                    USER,
                    f"--pwfile={secret.name}",
                    "--auth=scram-sha-256",
                    "--encoding=UTF8",
                    *locale,
                ],
                capture_output=True,
                text=True,
                env=server_env(),
            )
            if done.returncode == 0:
                return True
            shutil.rmtree(cfg.pgdata, ignore_errors=True)
            if attempt == len(locales) - 1:
                raise NativeError("initdb failed: " + done.stderr.strip()[-400:])
    return True


def _client_env(cfg):
    # PGPASSWORD, never argv: `ps` shows every user's command lines.
    return {**os.environ, "PGPASSWORD": cfg.password, "PGCONNECT_TIMEOUT": "5", "PGTZ": "UTC"}


def psql(bin_dir, cfg, sql, *, database=DATABASE, timeout=30, check=True):
    done = subprocess.run(
        [str(bin_dir / "psql"), *connection(cfg, database), "-XAt", "-v", "ON_ERROR_STOP=1", "-c", sql],
        capture_output=True,
        text=True,
        env=_client_env(cfg),
        timeout=timeout,
    )
    if check and done.returncode:
        raise NativeError("psql: " + done.stderr.strip()[-400:])
    return done.stdout.strip() if done.returncode == 0 else None


def connection(cfg, database=DATABASE):
    return ["-h", "127.0.0.1", "-p", str(cfg.pg_port), "-U", USER, "-d", database]


def is_ready(bin_dir, cfg):
    return (
        subprocess.run(
            [str(bin_dir / "pg_isready"), "-q", "-h", "127.0.0.1", "-p", str(cfg.pg_port), "-t", "1"],
            capture_output=True,
            timeout=5,
        ).returncode
        == 0
    )


def ensure_database(bin_dir, cfg):
    exists = psql(bin_dir, cfg, f"select 1 from pg_database where datname='{DATABASE}'", database="postgres")
    if not exists:
        psql(bin_dir, cfg, f"create database {DATABASE}", database="postgres")
