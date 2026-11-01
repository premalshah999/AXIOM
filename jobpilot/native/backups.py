"""Automatic copies of the database, because nothing else protects it.

It holds what cannot be recreated: every employer account and its password, each answer the applicant gave, and the guards
that stop a second application to the same posting. Until now the only copy was the `axiom backup` someone remembered to run
(2026-11-01), so a damaged disk or a bad upgrade meant applying twice from nothing. The supervisor takes one a day through that
same command, off the loop that watches the services, at low priority; the app keeps running because pg_dump reads one snapshot.

The copies live outside the data folder (~/axiom-backups) and hold the applicant's data, so the folder and files are private.
They are skipped rather than allowed to fill the disk, and the newest few are kept.
"""

import json
import os
import shutil
import time
from pathlib import Path

PREFIX = "axiom-auto-"
KEEP = 7
EVERY_SECONDS = 24 * 3600
# Retry after a failure or a skip, without waiting a day.
RETRY_SECONDS = 3600
FLOOR_BYTES = 3 * 1024**3


def folder():
    # AXIOM_BACKUP_DIR moves it: a test or an isolated instance must never read or delete the real folder (erase.py).
    return Path(os.environ.get("AXIOM_BACKUP_DIR") or Path.home() / "axiom-backups")


def marker(cfg):
    return cfg.home / "backup.json"


def read_marker(cfg):
    try:
        return json.loads(marker(cfg).read_text())
    except (OSError, ValueError):
        return {}


def record(cfg, **values):
    path = marker(cfg)
    path.write_text(json.dumps({"at": time.time(), **values}))
    path.chmod(0o600)


def copies(where=None):
    """The automatic copies, newest first. Copies someone made by hand are theirs and never counted or removed."""
    where = where or folder()
    return sorted(where.glob(PREFIX + "*.dump"), key=lambda p: p.name, reverse=True) if where.exists() else []


def due(cfg, now=None):
    state, now = read_marker(cfg), now or time.time()
    wait = RETRY_SECONDS if state.get("error") or state.get("skipped") else EVERY_SECONDS
    return now - float(state.get("at") or 0) >= wait


def room_for_one(where=None, free=None):
    """None when a copy fits, else why not. A copy needs about the size of the last one, and must leave the disk a floor."""
    where = where or folder()
    found = copies(where)
    size = found[0].stat().st_size if found else 0
    probe = where if where.exists() else where.parent
    free = shutil.disk_usage(probe).free if free is None else free
    if free < FLOOR_BYTES + 2 * size:
        return f"{free / 1e9:.1f} GB free is too little for another copy"
    return None


def prune(where=None, keep=KEEP):
    removed = []
    for old in copies(where)[keep:]:
        old.unlink(missing_ok=True)
        removed.append(old.name)
    return removed


def next_name():
    return f"{PREFIX}{time.strftime('%Y%m%d-%H%M%S')}.dump"


def summary(cfg, now=None):
    """One line for `axiom status`."""
    state, now = read_marker(cfg), now or time.time()
    found = copies()
    newest = f"{found[0]}, {found[0].stat().st_size / 1e9:.2f} GB" if found else "none yet"
    problem = state.get("error") or state.get("skipped")
    age = f"{(now - float(state['at'])) / 3600:.0f} h ago: " if state.get("at") else ""
    return newest + (f" (last try {age}{problem})" if problem else "")
