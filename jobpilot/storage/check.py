"""Test a database connection before anything changes: reachable, encrypted, new enough, the right kind of connection, allowed to
create what the app needs, closed to Supabase's public API, how many connections it will give us, and how long a round trip takes.

Nothing is left behind: the permission probe runs in a transaction that is rolled back, and the connection probe opens and closes.
Every failure is a sentence a person can act on; the connection string, and with it the password, is never in a message.
"""

import re
import socket
import statistics
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass

import psycopg
from sqlalchemy.engine import make_url

from . import choice

MIN_VERSION = 140000  # JSON operators, row_number windows and the DBOS system tables are all there from 14.
LATENCY_SAMPLES = 12
PROBE_CONNECTIONS = 24  # Enough for the largest pools the app would take; more only annoys a small pooler.
MIN_CONNECTIONS = 6  # Below this the app's own pools, DBOS and one command cannot all be open.
SCHEMAS = (choice.SCHEMA, "dbos")
API_ROLES = ("anon", "authenticated")


@dataclass
class Check:
    id: str
    label: str
    status: str  # ok | warn | fail | skip
    detail: str = ""


class Report:
    def __init__(self):
        self.checks: list[Check] = []
        self.facts: dict = {}

    def add(self, id, label, status, detail=""):
        self.checks.append(Check(id, label, status, detail))
        return status

    @property
    def ok(self):
        return not any(c.status == "fail" for c in self.checks)

    def as_dict(self):
        return {"ok": self.ok, "checks": [asdict(c) for c in self.checks], "facts": self.facts, **self.facts.get("verdict", {})}


def connect_kwargs(normalised: str, timeout=10) -> dict:
    url = make_url(normalised)
    query = dict(url.query)
    keep = ("sslmode", "keepalives", "keepalives_idle", "keepalives_interval", "keepalives_count")
    return {
        "host": url.host, "port": url.port or 5432, "user": url.username, "password": url.password, "dbname": url.database,
        "connect_timeout": timeout, "application_name": "axiom-test", **{k: str(query[k]) for k in keep if k in query},
    }


def explain(error: Exception, host: str, port) -> str:
    """What a connection failure means for the person who typed the string."""
    text = str(error).strip()
    low = text.lower()
    if "tenant or user not found" in low:
        return ("Supabase does not recognise this project and user. On the pooler the user is postgres.<project-ref>, with the "
                "region's pooler host; copy the whole string again from the Connect dialog.")
    if "password authentication failed" in low or "authentication failed" in low:
        return "The server rejected the user name or password. Check the password in the string (reset it in the project's Database settings if unsure)."
    if "no pg_hba.conf entry" in low:
        return "The server does not allow this computer or this kind of connection (its pg_hba.conf has no matching rule). Ask its administrator, or use SSL."
    if "does not support ssl" in low or "server does not support" in low:
        return "The server does not support encrypted connections. Enable SSL on it, or add ?sslmode=disable to the string if it is a private network."
    if "ssl" in low and ("required" in low or "must be" in low):
        return "The server requires an encrypted connection. Add ?sslmode=require to the string."
    if "could not translate host name" in low or "nodename nor servname" in low or "name or service not known" in low or "temporary failure in name resolution" in low:
        return f"Could not find the host '{host}'. Check the spelling in the string and this computer's internet connection."
    if "connection refused" in low:
        return f"Nothing is listening at {host}:{port}. Check the host and port, and that the server is running and allows this computer."
    if "timeout expired" in low or "timed out" in low:
        return f"No answer from {host}:{port} within 10 seconds. A firewall, a wrong port or a server that is asleep are the usual causes."
    if "network is unreachable" in low or "no route to host" in low:
        return (f"This computer has no route to {host}. A Supabase direct connection (db.<project>.supabase.co) is IPv6-only unless the "
                "project has the IPv4 add-on: use the Session pooler string instead.")
    if "does not exist" in low and "database" in low:
        return "That database does not exist on the server. Check the name at the end of the string."
    if "too many" in low or "max client" in low or "remaining connection slots" in low:
        return "The server has no free connections right now. Close other clients, or raise its connection limit, and test again."
    first = re.sub(r"\s+", " ", text.splitlines()[0]) if text else type(error).__name__
    return first[:300]


def reachable_addresses(host, port):
    try:
        found = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror:
        return None
    return {("IPv6" if f == socket.AF_INET6 else "IPv4") for f, *_ in found}


def table_names():
    from ..db import Base
    from ..features._store import FeatureBase
    from ..features.discovery import tables as _d  # noqa: F401 - registers the feature tables on FeatureBase
    from ..features.knowledge import tables as _k  # noqa: F401
    from ..features.resumes import tables as _r  # noqa: F401

    return sorted(set(Base.metadata.tables) | set(FeatureBase.metadata.tables))


def run(mode: str, raw_url: str, *, timeout=10, probe_connections=PROBE_CONNECTIONS) -> dict:
    report = Report()
    try:
        normalised = choice.normalise(raw_url, mode)
    except choice.DatabaseChoiceError as exc:
        report.add("string", "Connection string", "fail", str(exc))
        return report.as_dict()
    report.add("string", "Connection string", "ok", choice.label(normalised))
    kwargs = connect_kwargs(normalised, timeout)
    host, port = kwargs["host"], kwargs["port"]

    families = reachable_addresses(host, port)
    if families is None:
        report.add("address", "Address", "fail", f"Could not find the host '{host}'. Check the spelling and this computer's internet connection.")
        return report.as_dict()
    report.add("address", "Address", "ok", f"{host} resolves ({', '.join(sorted(families))}).")

    began = time.perf_counter()
    try:
        connection = psycopg.connect(autocommit=True, **kwargs)
    except Exception as exc:
        detail = explain(exc, host, port)
        if families == {"IPv6"} and "route" not in detail.lower() and "ipv6" not in detail.lower():
            detail += " This host only has an IPv6 address; if this computer has no IPv6, a Supabase project needs its Session pooler string or the IPv4 add-on."
        report.add("connect", "Connect", "fail", detail)
        return report.as_dict()
    connect_ms = (time.perf_counter() - began) * 1000
    report.add("connect", "Connect", "ok", f"Signed in in {connect_ms:.0f} ms.")
    report.facts["connect_ms"] = round(connect_ms)
    try:
        with connection:
            inspect_connection(report, connection, mode, kwargs, probe_connections)
    except Exception as exc:  # A probe that breaks is a finding, not a crash of the settings page.
        report.add("probe", "Checks", "fail", explain(exc, host, port))
    return report.as_dict()


def inspect_connection(report, conn, mode, kwargs, probe_connections):
    cur = conn.cursor()
    host = kwargs["host"]
    # Encryption.
    ssl = cur.execute("select ssl, version from pg_stat_ssl where pid = pg_backend_pid()").fetchone()
    encrypted = bool(ssl and ssl[0])
    report.facts["ssl"] = encrypted
    if encrypted:
        report.add("tls", "Encryption", "ok", f"Encrypted ({ssl[1]}).")
    elif choice.is_private_host(host):
        report.add("tls", "Encryption", "warn", "Not encrypted. Acceptable on a private network; anyone on it can read the traffic.")
    else:
        report.add("tls", "Encryption", "fail", "Not encrypted, and the host is on the internet: the password and every applicant answer would cross it in the clear.")
    # Version.
    number = int(cur.execute("show server_version_num").fetchone()[0])
    version = cur.execute("show server_version").fetchone()[0]
    report.facts["server_version"] = version
    if number < MIN_VERSION:
        report.add("version", "PostgreSQL version", "fail", f"PostgreSQL {version} is too old. AXIOM needs 14 or newer (17 is what it is tested on).")
    else:
        report.add("version", "PostgreSQL version", "ok", f"PostgreSQL {version}.")
    # A connection that keeps its session.
    pooler(report, conn, cur)
    # Who we are, and what that role may do.
    role, rolelimit, superuser = cur.execute(
        "select current_user, r.rolconnlimit, r.rolsuper from pg_roles r where r.rolname = current_user"
    ).fetchone()
    report.facts["role"] = role
    permissions(report, conn, cur)
    exposure(report, cur)
    # Durability: the application makes its own submit reservation durable per transaction, but a server with fsync off can corrupt itself.
    fsync = cur.execute("show fsync").fetchone()[0]
    if fsync != "on":
        report.add("durability", "Durability", "warn", "This server runs with fsync off, so a power cut can corrupt the whole database. Turn it on.")
    else:
        report.add("durability", "Durability", "ok", "Writes are flushed to disk before they are acknowledged.")
    limit(report, cur, kwargs, rolelimit, probe_connections)
    latency(report, conn, cur)
    existing(report, cur)
    report.facts["verdict"] = verdict(report)


def pooler(report, conn, cur):
    """A transaction-mode pooler hands every statement to whichever server connection is free: SET, prepared statements,
    advisory locks and LISTEN all stop meaning what they say. A session that keeps one backend for statements outside a transaction does."""
    cur.execute("set application_name = 'axiom-probe'")
    seen = [cur.execute("select pg_backend_pid(), current_setting('application_name')").fetchone() for _ in range(5)]
    if len({pid for pid, _ in seen}) > 1 or any(name != "axiom-probe" for _, name in seen):
        report.add("session", "Session connection", "fail",
                   "This looks like a transaction-mode pooler: consecutive statements ran on different server connections. AXIOM needs a "
                   "connection that keeps its session. For Supabase use the Session pooler (port 5432) or the Direct connection.")
    else:
        report.add("session", "Session connection", "ok", "Statements stay on one server connection, so session settings and locks work.")


def permissions(report, conn, cur):
    """Create what the app creates, in a transaction that is rolled back."""
    probe = f"axiom_probe_{int(time.time()) % 100000}"
    try:
        conn.autocommit = False
        cur.execute(f'create schema "{probe}"')
        cur.execute(f'create table "{probe}".t (id text primary key, v json, n integer generated always as identity)')
        cur.execute(f'create index on "{probe}".t (id text_pattern_ops)')
        cur.execute(f'insert into "{probe}".t (id, v) values (%s, %s::json)', ("a", '{"x": 1}'))
        cur.execute("select pg_advisory_xact_lock(72319002)")
        cur.execute("select set_config('synchronous_commit', 'on', true)")
        report.add("permissions", "Permissions", "ok", "Can create a schema, tables and indexes, write, and take the lock the app uses to never apply twice.")
    except psycopg.errors.InsufficientPrivilege:
        conn.rollback()
        try:
            usable = cur.execute("select has_schema_privilege(current_user, %s, 'CREATE')", (choice.SCHEMA,)).fetchone()[0]
        except Exception:
            usable = False
        if usable:
            report.add("permissions", "Permissions", "ok", f"This user may not create schemas, but the {choice.SCHEMA} schema exists and it may create tables in it.")
        else:
            report.add("permissions", "Permissions", "fail",
                       "This user may not create the schema the app needs. Use the project's postgres user, or ask for CREATE on the database.")
    except Exception as exc:
        report.add("permissions", "Permissions", "fail", explain(exc, "", ""))
    finally:
        try:
            conn.rollback()
        finally:
            conn.autocommit = True


def exposure(report, cur):
    """Supabase serves `public` through its REST API to anyone holding the anon key. The app keeps everything in a schema those roles
    cannot use; this says whether that is true here, and refuses a database where an API role could read it."""
    roles = [r for (r,) in cur.execute("select rolname from pg_roles where rolname = any(%s)", (list(API_ROLES),)).fetchall()]
    if not roles:
        report.add("exposure", "Closed to the public API", "ok", "No public API roles exist here, so nothing can publish the tables.")
        return
    problems = []
    schemas = [n for (n,) in cur.execute("select nspname from pg_namespace where nspname = any(%s)", (list(SCHEMAS),)).fetchall()]
    for role in roles:
        for schema in schemas:
            if cur.execute("select has_schema_privilege(%s, %s, 'USAGE')", (role, schema)).fetchone()[0]:
                problems.append(f"The {role} role can use the {schema} schema.")
    public = [t for (t,) in cur.execute(
        "select tablename from pg_tables where schemaname = 'public' and tablename = any(%s)", (table_names(),)
    ).fetchall()]
    if public:
        problems.append("These tables are in the public schema, which the anon key can read: " + ", ".join(public))
    if problems:
        report.add("exposure", "Closed to the public API", "fail", " ".join(problems) + " AXIOM will not run here until that is fixed.")
    else:
        report.add("exposure", "Closed to the public API", "ok",
                   "Applicant data goes in a private schema the anon and authenticated roles cannot use. (Checked in the database; "
                   "the project's Exposed schemas list is not read.)")


def limit(report, cur, kwargs, rolelimit, probe_connections):
    server_max = int(cur.execute("show max_connections").fetchone()[0])
    others = cur.execute("select count(*) from pg_stat_activity where backend_type = 'client backend' and pid <> pg_backend_pid()").fetchone()[0]
    opened, failure = open_many(kwargs, probe_connections - 1)
    allowed = opened + 1  # The one already open.
    report.facts.update(server_max_connections=server_max, role_connection_limit=rolelimit if rolelimit >= 0 else None,
                        other_clients=others, connections_allowed=allowed)
    pools = choice.size_pools(allowed)
    report.facts["pools"] = pools
    used = pools["pool"] + pools["overflow"] + pools["dbos"]
    if allowed < MIN_CONNECTIONS:
        report.add("limit", "Connection limit", "fail",
                   f"Only {allowed} connection(s) could be opened (the server allows {server_max} in all, {others} other clients are connected). "
                   f"AXIOM needs at least {MIN_CONNECTIONS}." + (f" The refusal said: {explain(failure, '', '')}" if failure else ""))
    elif allowed < probe_connections:
        report.add("limit", "Connection limit", "warn",
                   f"It allowed {allowed} connections at once (server maximum {server_max}, {others} used by others). AXIOM will use {used} "
                   f"({pools['pool']} + {pools['overflow']} for the app, {pools['dbos']} for its job queue) and leave {RESERVE_TEXT}. "
                   "On Supabase, raising Pool Size under Database > Connection pooling allows more workers.")
    else:
        report.add("limit", "Connection limit", "ok",
                   f"At least {allowed} connections available (server maximum {server_max}, {others} used by others). AXIOM will use {used}.")


RESERVE_TEXT = f"{choice.RESERVE} for commands"


def open_many(kwargs, count):
    """Open up to `count` more connections at once and report how many the server or its pooler accepted."""
    opened, failure, lock, held = 0, None, threading.Lock(), []

    def one(_):
        nonlocal opened, failure
        try:
            connection = psycopg.connect(autocommit=True, **{**kwargs, "connect_timeout": 6, "application_name": "axiom-test-probe"})
            connection.execute("select 1")
        except Exception as exc:
            with lock:
                failure = failure or exc
            return
        with lock:
            opened += 1
            held.append(connection)

    try:
        with ThreadPoolExecutor(max_workers=min(count, 12) or 1) as pool:
            list(pool.map(one, range(count)))
    finally:
        for connection in held:
            try:
                connection.close()
            except Exception:
                pass
    return opened, failure


def latency(report, conn, cur):
    samples = []
    for _ in range(LATENCY_SAMPLES):
        began = time.perf_counter()
        cur.execute("select 1")
        cur.fetchone()
        samples.append((time.perf_counter() - began) * 1000)
    samples.sort()
    median, worst = statistics.median(samples), samples[-1]
    commit = None
    try:
        # A transaction that has to be flushed to disk, to price the durable commit the app makes before every submit.
        conn.autocommit = False
        cur.execute("select pg_logical_emit_message(true, 'axiom', 'probe')")
        began = time.perf_counter()
        conn.commit()
        commit = (time.perf_counter() - began) * 1000
    except Exception:
        conn.rollback()
    finally:
        conn.autocommit = True
    report.facts["latency"] = {"median_ms": round(median, 1), "p95_ms": round(samples[int(len(samples) * 0.95) - 1], 1), "max_ms": round(worst, 1),
                               "commit_ms": None if commit is None else round(commit, 1), "samples": len(samples)}
    text = f"A query takes {median:.0f} ms (slowest {worst:.0f} ms)" + (f"; a durable commit {commit:.0f} ms." if commit is not None else ".")
    state = "ok" if median <= 40 else "warn" if median <= 120 else "fail"
    if state != "ok":
        text += " " + rate_workers(median)["text"]
    report.add("latency", "Speed", "warn" if state == "fail" else state, text)  # Slow is advice, not a reason to refuse.


def rate_workers(round_trip_ms: float) -> dict:
    """How many browser workers a round trip this long can drive, from the measurements in docs/NATIVE.md (section Remote database)."""
    if round_trip_ms <= 15:
        return {"workers": 9, "text": "Fast enough for all nine workers."}
    if round_trip_ms <= 40:
        return {"workers": 6, "text": "Fine for up to six workers; nine will work but each application is somewhat slower."}
    if round_trip_ms <= 120:
        return {"workers": 3, "text": "Slow for this app: use three workers or fewer, and expect each application to take longer."}
    return {"workers": 1, "text": "Too slow for more than one worker. Choose a database closer to this computer."}


def existing_counts(cur, schema=choice.SCHEMA) -> dict:
    """Whether `schema` already holds AXIOM data, and how much: a move must not overwrite it unasked."""
    found = cur.execute("select to_regclass(%s)", (f'"{schema}".runs',)).fetchone()[0]
    counts = {}
    if found:
        counts = {t: cur.execute(f'select count(*) from "{schema}"."{t}"').fetchone()[0] for t in ("runs", "jobs", "settings")}
    return {"has_data": bool(found and (counts.get("runs") or counts.get("jobs"))), "counts": counts}


def existing(report, cur):
    report.facts["existing"] = found = existing_counts(cur)
    counts = found["counts"]
    if found["has_data"]:
        report.add("existing", "Existing data", "warn",
                   f"This database already holds AXIOM data ({counts['jobs']:,} jobs, {counts['runs']:,} runs). Switching to it uses that data; "
                   "copying this computer's data over it needs the Replace option.")


def verdict(report):
    latency_ms = (report.facts.get("latency") or {}).get("median_ms")
    return {"workers": rate_workers(latency_ms) if latency_ms is not None else None, "pools": report.facts.get("pools")}
