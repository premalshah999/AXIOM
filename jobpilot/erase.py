"""Settings › Delete everything: remove what this installation knows about its owner, and leave it as it was on the first run.

The owner asked for a way out (2026-11-01). It is done by a separate process, so that the application is gone before anything is
deleted: a worker that is still clicking must not outlive the data it works from, and nothing may write a file back behind the
eraser. The app (api/routes/settings.py) validates the typed confirmation, pauses sending, starts this process, writes the marker and
exits. The Mac app's supervisor starts nothing while the marker names a living eraser (native/supervisor.py), then starts everything
as on a first run; without a supervisor, whoever starts the app next finds it empty.

What goes: the database (every table, and the workflow history), the data folder's files (saved pages, resumes, sessions, the
saved keys and the key that seals them, the database choice), the worker and twin Chrome profiles, and the database copies in the
backup folder. What stays, because the installation cannot start without it and it holds nothing about the owner: the cluster's
own folder, the supervisor's folder, and the access token this window signs in with. What cannot be removed from here is in
CANNOT, and the screen says it in the same words.
"""

import json
import os
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
BACKUP_PREFIX = "axiom-"
CANNOT = [
    "Applications already sent. The employers have them.",
    "Accounts AXIOM created at employers. Close them on the employers' sites; their emails to you stay in your own mailbox.",
]


def backup_folder(environ=None):
    """Where database copies are kept; AXIOM_BACKUP_DIR moves it (tests use it, so that no test touches the real folder)."""
    environ = os.environ if environ is None else environ
    return Path(environ.get("AXIOM_BACKUP_DIR") or Path.home() / "axiom-backups")


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
    from sqlalchemy import text

    from .db import Base, Database

    db = Database(url)
    try:
        with db.engine.begin() as connection:
            connection.execute(text("DROP SCHEMA IF EXISTS dbos CASCADE"))
        Base.metadata.drop_all(db.engine)
        say("Dropped every table and the workflow history.")
    finally:
        db.engine.dispose()


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


def run(config, *, after_pid=0, say=print, backups=None):
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
        for copy in folder.glob(BACKUP_PREFIX + "*.dump"):
            if not remove(copy, say):
                problems.append(copy.name)
        try:
            folder.rmdir()  # Only when nothing else of the owner's is in it.
        except OSError:
            pass
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
