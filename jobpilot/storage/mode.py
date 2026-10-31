"""The database choice that has to be readable before anything connects: Local, Supabase or Server.

Standard library only, because the native supervisor reads it to decide whether to start PostgreSQL at all. It holds no secret:
the connection strings are sealed apart from it (choice.py). `revision` rises with every save, so a supervisor can tell that
the choice changed since it planned what to run.
"""

import json
import os
import time
from pathlib import Path

MODES = ("local", "supabase", "server")
FILE = "database.json"


def read(data_dir) -> dict:
    try:
        raw = json.loads((Path(data_dir) / FILE).read_text())
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def write(data_dir, mode, **extra) -> dict:
    """Save the choice atomically (0600). Returns what was written."""
    if mode not in MODES:
        raise ValueError(f"The database choice must be one of {', '.join(MODES)}.")
    folder = Path(data_dir)
    previous = read(folder)
    value = {**extra, "mode": mode, "revision": int(previous.get("revision") or 0) + 1, "changed_at": time.time()}
    target = folder / FILE
    temporary = target.with_suffix(".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(value, f)
    os.replace(temporary, target)
    return value


def current(values, data_dir) -> str:
    """The choice in force: `DATABASE_MODE` in the environment or `.env` wins (it is the way back from anything), then the
    saved choice, then Local. A person who never opens the Database screen gets Local."""
    named = str((values or {}).get("DATABASE_MODE") or "").strip().lower()
    mode = named or str(read(data_dir).get("mode") or "local")
    if mode not in MODES:
        raise ValueError(f"The database choice is '{mode}', which is not one of {', '.join(MODES)}. Set DATABASE_MODE=local to go back.")
    return mode


def revision(data_dir) -> int:
    try:
        return int(read(data_dir).get("revision") or 0)
    except (TypeError, ValueError):
        return 0
