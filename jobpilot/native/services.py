"""The list of services the supervisor runs: read from services.toml and checked before anything starts.

AXIOM may split into several services, and the launcher must not need rewriting when it does. So nothing in the supervisor
knows a service by name; it knows this record. A mistake in the list stops `axiom start` with a message naming the entry
and the field, rather than leaving a half-started installation.
"""

import re
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from .env import LOCAL_HOSTS, NativeError

PACKAGED = Path(__file__).with_name("services.toml")
RESTART_POLICIES = ("always", "on-failure", "never")
INFRASTRUCTURE = ("postgres", "worker", "twin")  # Started by code of their own, and valid in `after`.
RESERVED = {*INFRASTRUCTURE, "supervisor"}  # `supervisor` is a log's name.
ALL_INTERFACES = {"0.0.0.0", "::", "[::]", "*"}
DEFAULT_STOP_GRACE = 10.0
FIELDS = {
    "name", "command", "port", "port_setting", "health_url", "restart", "required", "after", "restart_with", "drains",
    "stop_grace_seconds", "env",
}  # fmt: skip


@dataclass(frozen=True)
class Service:
    name: str
    command: tuple
    port: int | None = None
    health_url: str = ""
    restart: str = "always"
    required: bool = True
    after: tuple = ()
    restart_with: tuple = ()
    drains: bool = False
    stop_grace_seconds: float = DEFAULT_STOP_GRACE
    env: dict = field(default_factory=dict)


def source_file(cfg, release_dir=None):
    """The list that belongs to the code being run: a release's own, not the checkout's, which may have moved on to a
    service this release does not contain. AXIOM_SERVICES overrides it."""
    override = cfg.get("AXIOM_SERVICES")
    if override:
        path = Path(override).expanduser()
        return path if path.is_absolute() else cfg.root / path
    if release_dir:
        packaged = Path(release_dir) / "jobpilot" / "native" / "services.toml"
        if packaged.exists():
            return packaged
    return PACKAGED


def read(cfg, release_dir=None):
    path = source_file(cfg, release_dir)
    try:
        data = tomllib.loads(path.read_text())
    except OSError as exc:
        raise NativeError(f"Cannot read the service list {path}: {exc.strerror or exc}") from None
    except tomllib.TOMLDecodeError as exc:
        raise NativeError(f"The service list {path} is not valid TOML: {exc}") from None
    return parse(data, cfg)


def parse(data, cfg):
    entries = data.get("service")
    if not isinstance(entries, list) or not entries:
        raise NativeError("The service list has no [[service]] entries: there would be nothing to start.")
    found = [_service(entry, cfg) for entry in entries]
    names = [s.name for s in found]
    if len(set(names)) != len(names):
        raise NativeError(f"The service list names a service twice: {sorted(n for n in set(names) if names.count(n) > 1)}")
    _check_references(found)
    _check_ports(found, cfg)
    return found


def _service(entry, cfg):
    name = entry.get("name")
    if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9-]*", name):
        raise NativeError(f"A service needs a lowercase name, got {name!r}.")
    if name in RESERVED:
        raise NativeError(f"Service {name}: that name is reserved for something the supervisor runs itself.")

    def problem(what):
        return NativeError(f"Service {name}: {what}")

    unknown = sorted(set(entry) - FIELDS)
    if unknown:
        raise problem(f"unknown field(s) {unknown}. Known: {sorted(FIELDS)}")
    port = entry.get("port")
    setting = entry.get("port_setting")
    if setting and cfg.get(setting):
        try:
            port = int(cfg.get(setting))
        except ValueError:
            raise problem(f"{setting} must be a number, got {cfg.get(setting)!r}") from None
    if port is not None and not (isinstance(port, int) and 0 < port < 65536):
        raise problem(f"port must be a number from 1 to 65535, got {port!r}")
    values = {
        "python": sys.executable,
        "port": "" if port is None else str(port),
        "data_dir": str(cfg.data_dir),
        "root": str(cfg.root),
    }

    def fill(text, where):
        if not isinstance(text, str):
            raise problem(f"{where} must be text, got {text!r}")

        def one(match):
            key = match.group(1)
            if key not in values or (key == "port" and port is None):
                raise problem(f"{where} uses {{{key}}}, which has no value here")
            return values[key]

        return re.sub(r"\{([a-z_]+)\}", one, text)

    command = entry.get("command")
    if not isinstance(command, list) or not command:
        raise problem("command must be a list with the program first")
    argv = tuple(fill(part, "command") for part in command)
    if ALL_INTERFACES.intersection(argv):
        raise problem("the native app listens on 127.0.0.1 only, and its command binds every interface")
    health_url = fill(entry.get("health_url", ""), "health_url")
    if health_url:
        parts = urlsplit(health_url)
        if parts.scheme != "http" or parts.hostname not in LOCAL_HOSTS:
            raise problem(f"health_url must be local http, got {health_url}")
    restart = entry.get("restart", "always")
    if restart not in RESTART_POLICIES:
        raise problem(f"restart must be one of {list(RESTART_POLICIES)}, got {restart!r}")
    for key in ("required", "drains"):
        if not isinstance(entry.get(key, False), bool):
            raise problem(f"{key} must be true or false")
    grace = entry.get("stop_grace_seconds", DEFAULT_STOP_GRACE)
    if isinstance(grace, bool) or not isinstance(grace, (int, float)) or grace <= 0:
        raise problem(f"stop_grace_seconds must be a positive number, got {grace!r}")
    env = entry.get("env", {})
    if not isinstance(env, dict):
        raise problem("env must be a table")
    return Service(
        name=name,
        command=argv,
        port=port,
        health_url=health_url,
        restart=restart,
        required=entry.get("required", True),
        after=_names(entry, "after", problem),
        restart_with=_names(entry, "restart_with", problem),
        drains=entry.get("drains", False),
        stop_grace_seconds=float(grace),
        env={str(k): fill(v, "env") for k, v in env.items()},
    )


def _names(entry, key, problem):
    value = entry.get(key, [])
    if not isinstance(value, list) or not all(isinstance(n, str) for n in value):
        raise problem(f"{key} must be a list of names")
    return tuple(value)


def _check_references(found):
    known = {*INFRASTRUCTURE, *(s.name for s in found)}
    for service in found:
        for key in ("after", "restart_with"):
            missing = [n for n in getattr(service, key) if n not in known]
            if missing:
                raise NativeError(f"Service {service.name}: {key} names {missing}, which nothing starts.")
    waits = {s.name: set(s.after) for s in found}
    # Peel off whatever waits on nothing still in the list. Anything left waits on itself in a circle.
    while waits:
        free = [name for name, needs in waits.items() if not needs & set(waits)]
        if not free:
            raise NativeError(f"The services wait for each other in a circle: {sorted(waits)}")
        for name in free:
            del waits[name]


def _check_ports(found, cfg):
    taken = {cfg.pg_port: "the database"}
    for url, what in ((cfg.worker_cdp, "the worker Chrome"), (cfg.twin_cdp, "the twin Chrome")):
        if url:
            taken[urlsplit(url).port] = what
    for service in found:
        if service.port is None:
            continue
        if service.port in taken:
            raise NativeError(f"Service {service.name}: port {service.port} is already {taken[service.port]}'s.")
        taken[service.port] = f"service {service.name}"
