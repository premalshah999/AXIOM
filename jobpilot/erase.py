"""Settings › Delete everything: remove what this installation knows about its owner, and leave it as it was on the first run.

The owner asked for a way out (2026-11-01). It is done by a separate process, so that the application is gone before anything is
deleted: a worker that is still clicking must not outlive the data it works from, and nothing may write a file back behind the
eraser. The app (api/routes/settings.py) validates the typed confirmation, pauses sending, starts this process, writes the marker and
exits. The Mac app's supervisor starts nothing while the marker names a living eraser (native/supervisor.py), then starts everything
as on a first run; without a supervisor, whoever starts the app next finds it empty.

What goes: the database (every table, the workflow history, and on this computer the write-ahead log that still holds the rows), the
data folder's files (saved pages, resumes, sessions, the saved keys and the key that seals them, the database choice), the worker
and twin Chrome profiles, the database copies in the backup folder, the logs (they name employers and questions; the running programs
keep their files open, so those are emptied rather than deleted), and whatever the Mac app's settings file (`.env`) holds beyond the
machine's own settings. What stays, because the installation cannot start without it and it holds nothing about the owner: the
cluster's own folder, the supervisor's folder, the database password and the access token this window signs in with. What cannot be
removed from here is in CANNOT, and the screen says it in the same words.
"""

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit

PHRASE = "DELETE EVERYTHING"
MARKER = "erasing.json"
# Installation scaffolding inside the data folder: the empty database cluster, the supervisor's own state and releases, the
# token the window signs in with, and an index hint. None of it holds anything about the owner.
KEEP = {"postgres", "native", "access-token", ".metadata_never_index", MARKER}
CANNOT = [
    "Applications already sent. The employers have them.",
    "Accounts AXIOM created at employers. Close them on the employers' sites; their emails to you stay in your own mailbox.",
    "Your AI key and your Gmail app password. They still work at the services that issued them: revoke them there.",
    "Copies outside AXIOM: a Time Machine backup of this Mac, or a database copy you saved to another folder.",
]
# The settings of the Mac app's `.env` that are about this machine (desktop.first_config, ports and tuning). Everything else in it, on
# an installation moved in from another, can be an API key, a password or an address, and goes.
MACHINE_SETTINGS = re.compile(r"POSTGRES_PASSWORD|APP_TOKEN|APP_PORT|TIMEZONE|SHUTDOWN_DRAIN_SECONDS|[A-Z]+_WORKERS|AXIOM_[A-Z0-9_]+")


def backup_folder(environ=None):
    """Where database copies are kept; AXIOM_BACKUP_DIR moves it (tests use it, so that no test touches the real folder)."""
    environ = os.environ if environ is None else environ
    return Path(environ.get("AXIOM_BACKUP_DIR") or Path.home() / "axiom-backups")


def log_folders(config=None, environ=None):
    """Where the Mac app and the supervisor write their logs: AXIOM_LOG_DIR (the app sets it to ~/Library/Logs/AXIOM)."""
    environ = os.environ if environ is None else environ
    found = set()
    if config is not None:
        found.add(Path(config.data_dir) / "native" / "logs")  # Where the supervisor logs without the Mac app.
    if environ.get("AXIOM_LOG_DIR"):
        found.add(Path(environ["AXIOM_LOG_DIR"]).expanduser())
    return sorted(found)


def clear_logs(folder, say=None):
    """Empty the logs in `folder` and delete their rotated copies. A log is truncated in place, not deleted: the supervisor and the
    services hold it open for appending and would go on writing into a file nobody can see. The eraser's own log stays."""
    problems = []
    for path in sorted(Path(folder).glob("*")) if Path(folder).is_dir() else []:
        if path.name == log_path().name or path.is_dir() or path.is_symlink():
            continue
        try:
            if path.suffix == ".log":
                os.truncate(path, 0)
            elif ".log." in path.name:
                path.unlink()
        except OSError:
            problems.append(path.name)
            if say:
                say(f"Could not empty {path}.")
    return problems


def marker(data_dir):
    return Path(data_dir) / MARKER


def running(data_dir):
    """True while a living eraser holds the marker. A marker whose eraser died is stale and does not hold anything."""
    try:
        pid = int(json.loads(marker(data_dir).read_text()).get("pid") or 0)
    except (OSError, ValueError, AttributeError):
        return False
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def log_path():
    return Path(os.environ.get("AXIOM_LOG_DIR") or tempfile.gettempdir()) / "axiom-erase.log"


def begin(config, db):
    """Pause sending, start the eraser and write its marker. The caller exits the application a moment later."""
    data_dir = Path(config.data_dir)
    if running(data_dir):
        raise RuntimeError("Deleting is already under way.")
    # Before anything else, so that a failure later never leaves sending on.
    db.set_setting("control", {"paused": True, "auto_submit": False})
    db.flush_events()
    log = os.open(log_path(), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        helper = subprocess.Popen(
            [sys.executable, "-m", "jobpilot.cli", "erase", "--after-pid", str(os.getpid())],
            stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True, env=os.environ,
        )
    finally:
        os.close(log)
    target = marker(data_dir)
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump({"pid": helper.pid, "at": time.time()}, f)
    return helper.pid


def wait_gone(pid, seconds=30):
    """The application has exited (it is the eraser's parent's child, not ours). Past the deadline it is killed."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.2)
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    time.sleep(0.5)
    return False


def sqlite_file(url):
    if not url.startswith("sqlite"):
        return None
    path = urlsplit(url).path
    return Path(path[1:] if path.startswith("//") else path) if path else None


def wipe_database(url, say):
    """Remove every table AXIOM made, and the workflow history. SQLite: the file. PostgreSQL: the tables, so that the next start
    creates them empty. The same code serves the database on this computer and a remote one."""
    if not url:
        return
    if (file := sqlite_file(url)) is not None:
        for suffix in ("", "-wal", "-shm"):
            Path(str(file) + suffix).unlink(missing_ok=True)
        say(f"Removed the database file {file.name}.")
        return
    from sqlalchemy import inspect, text

    from .db import Base, Database
    from .features._store import FeatureBase, create_tables

    db = Database(url)
    try:
        create_tables(db.engine)  # Registers the feature tables on their metadata: the facts, answers and tailored resumes are there.
        with db.engine.begin() as connection:
            connection.execute(text("DROP SCHEMA IF EXISTS dbos CASCADE"))
        FeatureBase.metadata.drop_all(db.engine)
        Base.metadata.drop_all(db.engine)
        rest = inspect(db.engine).get_table_names()
        if on_this_computer(url):
            # This cluster is AXIOM's alone, so a table an older version left behind goes too. A server may hold someone else's.
            with db.engine.begin() as connection:
                for table in rest:
                    connection.execute(text('DROP TABLE IF EXISTS "{}" CASCADE'.format(table.replace('"', '""'))))
            forget_write_ahead_log(db.engine, say)
        elif rest:
            say(f"Left {len(rest)} tables on the server that are not AXIOM's: {', '.join(sorted(rest))[:300]}.")
        say("Dropped every table and the workflow history.")
    finally:
        db.engine.dispose()


def on_this_computer(url):
    return urlsplit(url).hostname in {"127.0.0.1", "localhost", "::1"}


def forget_write_ahead_log(engine, say):
    """Dropping a table leaves its rows in PostgreSQL's write-ahead log, kept for reuse, until new writes happen to cover them: a
    resume's text was still readable in pg_wal after everything was deleted. With recycling off, a checkpoint removes the finished
    segments instead of keeping them. A failure here leaves the database empty as promised, so it is reported and nothing more."""
    from sqlalchemy import text

    try:
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            connection.execute(text("ALTER SYSTEM SET wal_recycle = off"))
            connection.execute(text("SELECT pg_reload_conf()"))
            time.sleep(1)
            for _ in range(3):
                connection.execute(text("SELECT pg_switch_wal()"))
                connection.execute(text("CHECKPOINT"))
            connection.execute(text("ALTER SYSTEM RESET wal_recycle"))
            connection.execute(text("SELECT pg_reload_conf()"))
    except Exception as exc:
        say(f"The database's write-ahead log could not be cleared: {type(exc).__name__}.")


def forget_settings(environ=None, say=print):
    """Only what is about this machine stays in the Mac app's `.env`. An installation moved in from Docker keeps its API keys, bot token
    and passwords there, and they would come back on the next start. A checkout's `.env` is a developer's own and is left alone."""
    environ = os.environ if environ is None else environ
    folder = environ.get("AXIOM_CONFIG_DIR")
    path = Path(folder) / ".env" if folder else None
    if not path or not path.is_file():
        return []
    try:
        lines = path.read_text().splitlines()
        kept = [line for line in lines if line.startswith("#") or not line.strip() or MACHINE_SETTINGS.fullmatch(line.split("=", 1)[0].strip())]
        if len(kept) != len(lines):
            temporary = path.with_name(".env.erasing")
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(descriptor, "w") as f:
                f.write("\n".join(kept) + "\n")
            os.replace(temporary, path)
            say(f"Removed {len(lines) - len(kept)} saved settings from the settings file.")
    except OSError:
        say("Could not clean the settings file.")
        return [".env"]
    return []


def remove(path, say=None, tries=5):
    """Delete a file or a folder, again after a moment if something (a Chrome still closing) was writing into it."""
    path = Path(path)
    for attempt in range(tries):
        if not (path.exists() or path.is_symlink()):
            return True
        try:
            shutil.rmtree(path) if path.is_dir() and not path.is_symlink() else path.unlink()
        except OSError:
            time.sleep(0.5 * (attempt + 1))
    gone = not (path.exists() or path.is_symlink())
    if not gone and say:
        say(f"Could not remove {path}.")
    return gone


def profiles(config):
    """The worker and twin Chrome profiles: in the data folder unless .env put them elsewhere."""
    found = {Path(config.data_dir) / "desktop-browser", Path(config.data_dir) / "twin-browser"}
    for key in ("CHROME_WORKER_PROFILE", "CHROME_TWIN_PROFILE"):
        if os.environ.get(key):
            found.add(Path(os.environ[key]).expanduser())
    return sorted(found)


def run(config, *, after_pid=0, say=print, backups=None, logs=None):
    """Delete everything, in this order: wait for the application to be gone, close the Chromes, empty the database, the files, the
    copies. Returns a list of what could not be removed (empty when all went)."""
    problems = []
    data_dir = Path(config.data_dir)
    if after_pid and not wait_gone(after_pid):
        say("The application did not exit by itself and was stopped.")
    from .native.supervisor import stop_chrome_holding

    for profile in profiles(config):
        try:
            stop_chrome_holding(profile, say=say)
        except Exception as exc:  # A Chrome that will not close is reported, and its folder is still tried.
            say(f"Could not close the Chrome using {profile.name}: {exc}")
    urls = [config.database_url, config.dbos_database_url]
    for url in dict.fromkeys(urls):
        try:
            wipe_database(url, say)
        except Exception as exc:
            problems.append(f"the database ({type(exc).__name__}: {str(exc)[:160]})")
            say(f"The database could not be emptied: {exc}")
    for entry in sorted(data_dir.iterdir()) if data_dir.exists() else []:
        if entry.name in KEEP:
            continue
        if not remove(entry, say):
            problems.append(entry.name)
    for profile in profiles(config):
        if not remove(profile, say):
            problems.append(profile.name)
    state = data_dir / "native"
    for name in ("backup.json",):
        remove(state / name)
    folder = Path(backups) if backups else backup_folder()
    if folder.exists():
        # Every database copy in AXIOM's own folder, whatever it was called: a copy made by hand (`axiom backup name.dump`) holds
        # the same employer passwords and answers.
        for copy in [*folder.glob("*.dump"), *folder.glob("*.dump.partial")]:
            if not remove(copy, say):
                problems.append(copy.name)
        try:
            folder.rmdir()  # Only when nothing else of the owner's is in it.
        except OSError:
            pass
    problems += forget_settings(say=say)
    for where in [Path(logs)] if logs else log_folders(config):
        problems += clear_logs(where, say)
    say("Done." if not problems else "Done, except: " + ", ".join(problems))
    return problems


def main(config, after_pid=0):
    """The eraser's entry point. The marker goes last, success or not: a failure must not leave the installation unable to start."""
    data_dir = Path(config.data_dir)

    def say(line):
        print(time.strftime("%H:%M:%S ") + line, flush=True)

    try:
        problems = run(config, after_pid=after_pid, say=say)
    finally:
        marker(data_dir).unlink(missing_ok=True)
    return 1 if problems else 0
