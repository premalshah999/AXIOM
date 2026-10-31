"""The database choice: Local (the default, nothing to configure), Supabase, or a Server (any PostgreSQL URL).

The choice cannot live in the database it names, so it is outside it. Which one is in force is DATA_DIR/database.json (mode.py:
no secret in it, and the supervisor reads it before it decides whether to start PostgreSQL). The connection strings carry
passwords, so they are sealed in DATA_DIR/database.enc with the key that seals mailbox and employer passwords
(connections.py) and are never logged, returned to the browser or written into the app's environment. `DATABASE_MODE`,
`SUPABASE_DB_URL` and `SERVER_DB_URL` in the environment or `.env` win over what the app saved, so `DATABASE_MODE=local` is always
the way back.

An installation that chose a remote database and cannot use it refuses to start. Falling back to Local would write new
applications where the owner believes nothing is, and they would be missing from the database that is meant to hold them.
"""

import ipaddress
import json
import os
import re
import time
from urllib.parse import unquote

from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

from . import mode as saved_mode
from .mode import MODES

SEALED = "database.enc"
REMOTE = ("supabase", "server")
URL_FIELDS = {"supabase": "supabase_db_url", "server": "server_db_url"}
# Supabase publishes `public` through its REST API to anyone holding the anon key. This schema is not published, and a table
# created in it by a later release is private without further steps (remote_db.install). A server of your own gets the same, so
# that the application's tables never mix with whatever else that database holds.
SCHEMA = "axiom"
TRANSACTION_POOL_PORT = 6543
SCHEMES = {"postgres", "postgresql", "postgresql+psycopg"}
# A home connection and the routers in front of it drop idle sockets without telling anyone.
KEEPALIVE = {"connect_timeout": "10", "keepalives": "1", "keepalives_idle": "30", "keepalives_interval": "10",
             "keepalives_count": "3"}
# Supabase's session pooler allows 15 clients on the smallest compute: the application's pool and overflow, and DBOS's.
DEFAULT_POOLS = {"pool": 6, "overflow": 4, "dbos": 5}
# Connections left for `jobpilot` commands and a second look from the dashboard while the app holds the rest.
RESERVE = 2
PRIVATE_SUFFIXES = (".local", ".lan", ".internal", ".home.arpa", ".localdomain")


class DatabaseChoiceError(ValueError):
    """The database settings cannot work; the message says what to change."""


def _cipher(config):
    from ..connections import _cipher as cipher

    return cipher(config)


def sealed(config) -> dict:
    path = config.data_dir / SEALED
    if not path.exists():
        return {}
    from cryptography.fernet import InvalidToken

    try:
        values = json.loads(_cipher(config).decrypt(path.read_bytes()))
    except (InvalidToken, ValueError, OSError):
        return {}
    return {k: str(v) for k, v in values.items() if k in URL_FIELDS.values() and str(v).strip()}


def _seal(config, values: dict) -> None:
    path = config.data_dir / SEALED
    temporary = path.with_suffix(".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(_cipher(config).encrypt(json.dumps(values).encode()))
    os.replace(temporary, path)


def is_private_host(host: str) -> bool:
    host = (host or "").strip("[]").lower()
    if not host:
        return False
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return host == "localhost" or "." not in host or host.endswith(PRIVATE_SUFFIXES)
    return address.is_loopback or address.is_private or address.is_link_local


def normalise(raw: str, mode: str) -> str:
    """The connection string in the form the app connects with. DatabaseChoiceError when it cannot work, in words a person can act on."""
    raw = (raw or "").strip()
    where = "Supabase" if mode == "supabase" else "server"
    if not raw:
        raise DatabaseChoiceError(f"Paste the {where} connection string. It starts with postgresql://")
    if "[YOUR-PASSWORD]" in raw.upper():
        raise DatabaseChoiceError("Replace [YOUR-PASSWORD] in the connection string with the database password.")
    try:
        url = make_url(raw)
    except ArgumentError:
        raise DatabaseChoiceError(
            "The connection string could not be read. It looks like postgresql://user:password@host:5432/database, and "
            "special characters in the password (@ : / ? #) must be written as %40 %3A %2F %3F %23."
        ) from None
    if url.drivername not in SCHEMES:
        raise DatabaseChoiceError("The connection string must start with postgresql://")
    if not url.host:
        raise DatabaseChoiceError("The connection string needs a host name, for example db.example.com.")
    if mode == "supabase" and not url.password:
        raise DatabaseChoiceError("The connection string needs the database password: the one you set when you created the project.")
    if url.port == TRANSACTION_POOL_PORT:
        # This app keeps session state (prepared statements, SET search_path, advisory locks), which a transaction pooler
        # hands to whichever connection comes next.
        raise DatabaseChoiceError(
            "Port 6543 is Supabase's transaction pooler. It breaks prepared statements, advisory locks and session settings, "
            "which AXIOM relies on to never apply twice. In Supabase open Connect and copy the Session pooler string "
            "(port 5432) or the Direct connection instead."
        )
    query = {k: v for k, v in url.query.items() if k not in {"pgbouncer", "supa"} and not k.startswith("axiom_")}
    wanted = query.get("sslmode")
    if mode == "supabase":
        if wanted not in {"require", "verify-ca", "verify-full"}:
            query["sslmode"] = "require"
    elif not wanted and not is_private_host(url.host):
        query["sslmode"] = "require"  # A server on the internet is encrypted unless the owner says otherwise in the string.
    url = url.set(drivername="postgresql+psycopg", query={**KEEPALIVE, **query})
    return url.render_as_string(hide_password=False)


def mask_url(value: str) -> dict:
    """Where a connection string points, never the password: for the screen and for logs."""
    try:
        url = make_url(value)
    except ArgumentError:
        return {"host": "", "port": None, "database": "", "user": ""}
    return {"host": url.host or "", "port": url.port, "database": url.database or "", "user": unquote(url.username or "")}


def label(value: str) -> str:
    found = mask_url(value)
    return f"{found['user']}@{found['host']}:{found['port'] or 5432}/{found['database']}" if found["host"] else ""


def size_pools(limit=None) -> dict:
    """Connections for the application's pool, its overflow and DBOS from how many the database lets this app hold. None: the
    defaults for the smallest Supabase compute, which are also safe anywhere."""
    if not limit:
        return dict(DEFAULT_POOLS)
    usable = max(int(limit) - RESERVE, 3)
    dbos = min(10, max(2, round(usable * 0.3)))
    app = min(30, max(usable - dbos, 2))
    pool = min(10, max(1, round(app * 0.6)))
    return {"pool": pool, "overflow": min(20, max(app - pool, 0)), "dbos": dbos}


def _from_env(config, name) -> bool:
    return name in config.model_fields_set and getattr(config, name) not in ("", None)


def pools_in_force(config, mode) -> dict:
    saved = (saved_mode.read(config.data_dir).get("pools") or {}) if mode in REMOTE else {}
    pools = {**DEFAULT_POOLS, **{k: int(v) for k, v in saved.items() if k in DEFAULT_POOLS and str(v).isdigit()}}
    if config.db_pool_size is not None:
        pools["pool"] = config.db_pool_size
    if config.db_pool_overflow is not None:
        pools["overflow"] = config.db_pool_overflow
    if config.dbos_pool_size is not None:
        pools["dbos"] = config.dbos_pool_size
    return pools


def urls(config, mode) -> tuple[str, str]:
    """(application URL, DBOS URL) for a remote choice. The application's carries its schema and pool for db.Database."""
    base = normalise(getattr(config, URL_FIELDS[mode]), mode)
    pools = pools_in_force(config, mode)
    app = make_url(base).update_query_dict(
        {"axiom_schema": SCHEMA, "axiom_pool": str(pools["pool"]), "axiom_overflow": str(pools["overflow"])}
    )
    return app.render_as_string(hide_password=False), base


def apply(config):
    """Lay the saved choice under the environment's, and point the database at it when it is not Local."""
    if config._local_database_urls is not None:
        return config  # prepare() runs more than once on one object.
    config._local_database_urls = (config.database_url, config.dbos_database_url)
    try:
        chosen = (config.database_mode if _from_env(config, "database_mode") else saved_mode.current({}, config.data_dir)).lower()
    except ValueError as exc:
        raise DatabaseChoiceError(str(exc)) from None
    if chosen not in MODES:
        raise DatabaseChoiceError(f"DATABASE_MODE is '{chosen}', which is not one of {', '.join(MODES)}.")
    object.__setattr__(config, "database_mode", chosen)  # Not setattr: it would mark the value as from the environment.
    config._database_applied = (chosen, saved_mode.revision(config.data_dir))
    if chosen == "local":
        return config
    saved = sealed(config)
    for name in URL_FIELDS.values():
        if not _from_env(config, name) and name in saved:
            object.__setattr__(config, name, saved[name])
    try:
        config.database_url, config.dbos_database_url = urls(config, chosen)
    except DatabaseChoiceError as exc:
        raise DatabaseChoiceError(
            f"The database is set to {'Supabase' if chosen == 'supabase' else 'a server'} but cannot be used: {exc} "
            "To go back to the database on this computer, set DATABASE_MODE=local in .env or run: jobpilot database use-local"
        ) from None
    return config


def local_urls(config) -> tuple[str, str]:
    """The database this installation used before a remote one replaced it: what a switch copies from or back to."""
    return config._local_database_urls or (config.database_url, config.dbos_database_url)


def save(config, mode: str, url: str = "", pools: dict | None = None) -> dict:
    """Keep a choice. `url` is sealed (blank keeps the one already saved); `pools` come from a connection test. Takes effect
    at the next start. Returns what database.json now says."""
    if mode not in MODES:
        raise DatabaseChoiceError(f"The database choice must be one of {', '.join(MODES)}.")
    before = saved_mode.read(config.data_dir)
    if mode in REMOTE:
        values = sealed(config)
        if url.strip():
            values[URL_FIELDS[mode]] = normalise(url, mode)
        elif URL_FIELDS[mode] not in values and not getattr(config, URL_FIELDS[mode]):
            raise DatabaseChoiceError(f"Paste the {'Supabase' if mode == 'supabase' else 'server'} connection string first.")
        _seal(config, values)
    extra = {k: v for k, v in before.items() if k in {"pools", "previous"}}
    was = before.get("mode") or (getattr(config, "_database_applied", None) or ("local",))[0]
    if was != mode:
        extra["previous"] = was
    if pools:
        extra["pools"] = {k: int(pools[k]) for k in DEFAULT_POOLS if k in pools}
    elif mode == "local":
        extra.pop("pools", None)
    return saved_mode.write(config.data_dir, mode, **extra)


def keep_url(config, mode: str, url: str) -> None:
    """Seal a connection string for `mode` without choosing it. A switch reads it from here, so that it is never on a command line."""
    values = sealed(config)
    values[URL_FIELDS[mode]] = normalise(url, mode)
    _seal(config, values)


def forget(config, mode: str) -> None:
    """Remove a saved connection string (its password with it)."""
    values = sealed(config)
    values.pop(URL_FIELDS[mode], None)
    _seal(config, values)


def url_for(config, mode: str) -> str:
    """The normalised string for `mode`, from the environment or the sealed file; '' when none is saved."""
    raw = getattr(config, URL_FIELDS[mode], "") or sealed(config).get(URL_FIELDS[mode], "")
    return normalise(raw, mode) if raw else ""


def view(config) -> dict:
    """What the Database screen shows. Never a password."""
    plain = saved_mode.read(config.data_dir)
    in_env = _from_env(config, "database_mode")
    applied, revision = getattr(config, "_database_applied", None) or (config.database_mode, saved_mode.revision(config.data_dir))
    try:
        chosen = saved_mode.current({"DATABASE_MODE": config.database_mode} if in_env else {}, config.data_dir)
    except ValueError:
        chosen = applied
    saved = sealed(config)
    options = {"local": {"configured": True, "target": ""}}
    for name in REMOTE:
        raw = getattr(config, URL_FIELDS[name], "") or saved.get(URL_FIELDS[name], "")
        options[name] = {"configured": bool(raw), "target": label(raw) if raw else ""}
    return {
        "mode": applied,
        "chosen": chosen,
        "revision": revision,
        "restart_needed": (chosen, saved_mode.revision(config.data_dir)) != (applied, revision),
        "source": "env" if in_env else "app" if plain.get("mode") else "default",
        "previous": plain.get("previous") or "",
        "options": options,
        "pools": pools_in_force(config, applied),
        "changed_at": plain.get("changed_at") or 0,
        "checked_at": time.time(),
    }


def native_connection(values, data_dir) -> dict | None:
    """libpq environment variables for the remote database in force, for the native tools that run `psql` (the hold, the
    active-run count that keeps the Mac awake). None for Local. The password goes in the environment, never on a command line.
    `values` is the environment over `.env`, as native/env.py reads it."""
    from types import SimpleNamespace
    from pathlib import Path

    mode = saved_mode.current(values, data_dir)
    if mode == "local":
        return None
    config = SimpleNamespace(data_dir=Path(data_dir), mail_encryption_key=(values or {}).get("MAIL_ENCRYPTION_KEY", ""))
    name = URL_FIELDS[mode]
    raw = (values or {}).get(name.upper()) or sealed(config).get(name, "")
    try:
        url = make_url(normalise(raw, mode))
    except DatabaseChoiceError as exc:
        raise DatabaseChoiceError(f"The database is set to {mode} but cannot be used: {exc}") from None
    return {
        "PGHOST": url.host, "PGPORT": str(url.port or 5432), "PGUSER": unquote(url.username or ""), "PGPASSWORD": unquote(url.password or ""),
        "PGDATABASE": url.database or "postgres", "PGSSLMODE": url.query.get("sslmode", "prefer"), "PGCONNECT_TIMEOUT": "10",
        "PGOPTIONS": f"-c search_path={SCHEMA}", "PGTZ": "UTC",
    }
