"""The one process that owns the rest: PostgreSQL, the worker and twin Chromes, caffeinate, and every service in
services.toml. Today that list is the single app; nothing here knows a service by name, so it can grow without this file.

It does what Compose and Docker's init did for the containers:

* `restart: unless-stopped`: a child that exits is started again, as its restart policy says, and
  `POST /api/system/restart` (exit 0) is a restart. A child that keeps dying within a minute stops the installation with a
  message instead of looping forever.
* `stop_grace_period`: a stop goes in reverse dependency order, so each service drains with what it depends on still up: the
  app with the browsers and the database, for the same 180 s.
* `init: true`: each child leads its own process group, and whatever a dead child left behind (Playwright's driver, a
  headless Chromium) is killed with the group. There is no container to tear down that would do it otherwise.
* `depends_on`: a service starts once what it names in `after` answers, and restarts when something in `restart_with` does.

A single thread, a one-second tick and no pipes: a child logs straight to a file, so it can outlive the supervisor without
meeting a broken pipe, and `axiom stop` can still stop it from the pids in the state file.
"""

import fcntl
import http.client
import json
import os
import re
import resource
import shutil
import signal
import subprocess
import time
from urllib.parse import urlsplit

from . import pg, release, services
from .env import NativeError, load, prepare, system_chrome

STABLE_SECONDS = 60.0
MAX_STRIKES = 5
HEALTH_SECONDS = 10.0
ACTIVE_SECONDS = 20.0
# Autopilot gaps (a retry due in two minutes) must not let the Mac sleep between applications.
SLEEP_RELEASE_SECONDS = 300.0
POWER_SECONDS = 60.0
ROTATE_SECONDS = 30.0
LOG_BYTES, LOG_KEEP = 10 * 1024 * 1024, 3  # compose.yaml's json-file limits.
CHROME_GRACE, POSTGRES_GRACE, CHROME_WAIT = 10.0, 30.0, 30.0
ACTIVE_RUNS = "select count(*) from runs where state in ('queued','running','submitting')"
NOFILE = (65536, 10240)


def http_ok(url, timeout=3.0):
    """No proxies and no urllib: macOS proxy lookup loads system frameworks into what should be a small process."""
    parts = urlsplit(url)
    connection = http.client.HTTPConnection(parts.hostname, parts.port, timeout=timeout)
    try:
        connection.request("GET", parts.path or "/")
        return connection.getresponse().status == 200
    except OSError:
        return False
    finally:
        connection.close()


def port_in_use(port):
    import socket

    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def process_start(pid):
    """When a process began, so that a pid in the state file is never mistaken for a new process that reused it."""
    # A fixed locale and zone: the supervisor and the `axiom` asking about it can have different environments.
    env = {**os.environ, "LC_ALL": "C", "TZ": "UTC"}
    done = subprocess.run(["ps", "-o", "lstart=", "-p", str(pid)], capture_output=True, text=True, timeout=5, env=env)
    return done.stdout.strip()


def same_process(pid, started):
    return bool(pid and started) and process_start(pid) == started


def power_status():
    """{'battery': bool, 'percent': int, 'remaining': '0:50'}, or {} where pmset does not exist."""
    if not shutil.which("pmset"):
        return {}
    try:
        out = subprocess.run(["pmset", "-g", "batt"], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.TimeoutExpired):
        return {}
    level = re.search(r"(\d+)%", out)
    remaining = re.search(r"(\d+:\d+) remaining", out)
    return {
        "battery": "Battery Power" in out,
        "percent": int(level.group(1)) if level else None,
        "remaining": remaining.group(1) if remaining else "",
    }


def docker_instances():
    """Running containers of this image. Two installations applying to the same jobs from two databases would send every
    application twice, so the native app refuses to start beside one."""
    if not shutil.which("docker"):
        return []
    try:
        done = subprocess.run(
            ["docker", "ps", "--filter", "label=org.opencontainers.image.title=AXIOM", "--format", "{{.Names}}"],
            capture_output=True,
            text=True,
            timeout=8,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    return done.stdout.split() if done.returncode == 0 else []


def chrome_argv(cfg, script, port_var, profile_var, url, profile, extra=""):
    """The Chrome command line of scripts/chrome-*.sh, which own the flags. None when something already answers."""
    port = str(urlsplit(url).port)
    env = {**os.environ, "DRY_RUN": "1", port_var: port, profile_var: str(profile)}
    done = subprocess.run(
        ["sh", str(cfg.root / "scripts" / script)], capture_output=True, text=True, env=env, timeout=30
    )
    if done.returncode:
        raise NativeError(done.stderr.strip() or f"scripts/{script} failed")
    lines = done.stdout.splitlines()
    if not lines or f"--remote-debugging-port={port}" not in lines:
        return None  # "already running": it does nothing when the port answers.
    profile_dir = next(line.split("=", 1)[1] for line in lines if line.startswith("--user-data-dir="))
    os.makedirs(profile_dir, mode=0o700, exist_ok=True)
    return [lines[0], *extra.split(), *lines[1:]]


class Child:
    def __init__(self, name, argv, *, env=None, cwd=None, log=None, required=True, after=(), ready=None,
                 stop_signal=signal.SIGTERM, grace=10.0, final_signal=signal.SIGKILL, url=None, service=False,
                 restart="always", restart_with=(), health_url="", port=None):
        self.name, self.argv, self.env, self.cwd, self.log = name, argv, env, cwd, log
        self.required, self.after, self.ready = required, after, ready
        self.stop_signal, self.grace, self.final_signal, self.url = stop_signal, grace, final_signal, url
        self.service, self.restart, self.restart_with = service, restart, restart_with
        self.health_url, self.port = health_url, port
        self.healthy, self.healthy_since = None, None
        self.proc = None
        self.state = "down"  # down | starting | up | external | done | failed
        self.next_start = 0.0
        self.started = 0.0
        self.strikes = 0
        self.restarts = 0
        self.message = ""
        self.stopping = False
        self.kill_at = None
        self.lstart = ""

    @property
    def pid(self):
        return self.proc.pid if self.proc else None

    def spawn(self):
        argv = self.argv() if callable(self.argv) else self.argv
        if argv is None:  # Chrome already answers on this port: someone else's, so never started or stopped here.
            self.state = "external"
            return
        out = os.open(self.log, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600) if self.log else subprocess.DEVNULL
        try:
            env = self.env() if callable(self.env) else self.env
            self.proc = subprocess.Popen(
                argv,
                env=env,
                cwd=self.cwd,
                stdin=subprocess.DEVNULL,
                stdout=out,
                stderr=subprocess.STDOUT if self.log else subprocess.DEVNULL,
                start_new_session=True,
            )
        finally:
            if self.log:
                os.close(out)
        self.started = time.monotonic()
        self.lstart = process_start(self.proc.pid)
        self.state, self.message = "starting", ""

    def signal(self, sig):
        """To the process, not its group. The app must drain with Playwright's driver still alive: under Compose too the
        SIGTERM reached only the app (Docker's init forwards to its one child)."""
        if self.proc:
            try:
                self.proc.send_signal(sig)
            except ProcessLookupError:
                pass

    def request_stop(self, *, now):
        if self.proc and not self.stopping:
            self.stopping = True
            self.kill_at = now + self.grace
            self.signal(self.stop_signal)

    def reap_group(self, pid, wait=2.0):
        """Whatever the dead child left in its process group: an orphaned Playwright driver or Chromium would otherwise
        hold memory until reboot."""
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(pid, sig)
            except (ProcessLookupError, PermissionError):
                return
            if sig == signal.SIGKILL:
                return
            deadline = time.monotonic() + wait
            while time.monotonic() < deadline:
                try:
                    os.killpg(pid, 0)
                except (ProcessLookupError, PermissionError):
                    return
                time.sleep(0.1)


def in_dependency_order(children):
    """Each child after what it waits for, otherwise in list order. Reversed, this is the order to stop them in."""
    ordered, placed, pending = [], set(), list(children)
    while pending:
        ready = next((c for c in pending if all(d.name in placed or d not in children for d in c.after)), None)
        if ready is None:  # A circle. services.py refuses one at start, and a stop must never hang on it anyway.
            return ordered + pending
        ordered.append(ready)
        placed.add(ready.name)
        pending.remove(ready)
    return ordered


class Supervisor:
    def __init__(self, cfg, children=None, *, say=None):
        self.cfg = cfg
        self.say = say or (lambda line: print(time.strftime("%Y-%m-%d %H:%M:%S ") + line, flush=True))
        self.pg_bin = None
        self.children = children
        self.phase = "starting"
        self.reason = ""
        self.stop_reason = ""
        self.failure = ""
        self.restart_requested = False
        self.began = time.monotonic()
        self.began_at = time.time()
        self.timings = {}
        self.caffeinate = None
        self.active = None
        self.last_active = None
        self.power = {}
        self.next_tick = {}
        self.written = 0.0
        self.lstart = process_start(os.getpid())

    # ---- what runs ---------------------------------------------------------------------------------------------------

    def plan(self):
        cfg = self.cfg
        self.pg_bin = pg.find_bin(cfg)
        pg.init_cluster(self.pg_bin, cfg)
        database = Child(
            "postgres",
            pg.server_argv(self.pg_bin, cfg),
            env=pg.server_env(),
            log=cfg.logs / "postgres.log",
            ready=lambda: pg.is_ready(self.pg_bin, cfg),
            # SIGINT is "fast": it disconnects clients and checkpoints. SIGQUIT is the last resort. Never SIGKILL: that
            # would leave shared memory behind and force crash recovery.
            stop_signal=signal.SIGINT,
            grace=POSTGRES_GRACE,
            final_signal=signal.SIGQUIT,
        )
        browsers = []
        if cfg.worker_cdp:
            browsers.append(
                Child(
                    "worker",
                    lambda: chrome_argv(
                        cfg,
                        "chrome-worker.sh",
                        "CHROME_WORKER_PORT",
                        "CHROME_WORKER_PROFILE",
                        cfg.worker_cdp,
                        cfg.get("CHROME_WORKER_PROFILE") or cfg.data_dir / "desktop-browser",
                        cfg.get("AXIOM_WORKER_CHROME_EXTRA_ARGS"),
                    ),
                    ready=lambda: http_ok(cfg.worker_cdp + "/json/version"),
                    url=cfg.worker_cdp,
                    grace=CHROME_GRACE,
                )
            )
        if cfg.twin_cdp:
            browsers.append(
                Child(
                    "twin",
                    lambda: chrome_argv(
                        cfg,
                        "chrome-twin.sh",
                        "CHROME_TWIN_PORT",
                        "CHROME_TWIN_PROFILE",
                        cfg.twin_cdp,
                        cfg.get("CHROME_TWIN_PROFILE") or cfg.data_dir / "twin-browser",
                    ),
                    required=False,  # It only mints hCaptcha tokens: applications go on without it.
                    ready=lambda: http_ok(cfg.twin_cdp + "/json/version"),
                    url=cfg.twin_cdp,
                    grace=CHROME_GRACE,
                )
            )
        infrastructure = [database, *browsers]
        listed = services.read(cfg, release.current(cfg))
        by_name = {c.name: c for c in infrastructure}
        running = [self.service_child(s, by_name) for s in listed]
        for child in running:
            # A name in `after` that this installation does not run (no twin Chrome configured) is nothing to wait for.
            child.after = tuple(by_name[n] for n in child.after if n in by_name)
        return in_dependency_order([*infrastructure, *running])

    def service_child(self, service, by_name):
        """A service as the supervisor runs it: nothing below this line depends on which service it is."""
        cfg = self.cfg

        def environment():
            port = {"APP_PORT": str(service.port)} if service.port else {}
            return {**cfg.app_environment(release.current(cfg)), **port, **service.env}

        child = Child(
            service.name,
            list(service.command),
            env=environment,
            cwd=cfg.root,  # `.env` is read from here; the code comes from PYTHONPATH (env.py).
            log=cfg.logs / f"{service.name}.log",
            required=service.required,
            ready=(lambda: http_ok(service.health_url)) if service.health_url else None,
            after=service.after,  # Names until plan() has them all; then the children themselves.
            grace=float(cfg.stop_grace) if service.drains else service.stop_grace_seconds,
            service=True,
            restart=service.restart,
            restart_with=service.restart_with,
            health_url=service.health_url,
            port=service.port,
        )
        by_name[service.name] = child
        return child

    def preflight(self):
        cfg = self.cfg
        marker = cfg.migration / "db-restore.started"
        if marker.exists():
            raise NativeError(
                "A database restore did not finish, so this database is incomplete. Run `axiom migrate db` again."
            )
        if cfg.get("APP_BIND", "127.0.0.1") not in {"127.0.0.1", "localhost", ""}:
            self.say("APP_BIND is ignored here: the native app listens on 127.0.0.1 only.")
        instances = docker_instances()
        allowed = cfg.get("AXIOM_ALLOW_DOCKER") == "1"
        if instances and not allowed:
            raise NativeError(
                f"AXIOM is also running in Docker ({', '.join(instances)}). Two installations would apply to the same "
                "jobs twice. Stop it first: docker compose stop app"
            )
        listed = services.read(cfg, release.current(cfg))
        ports = [(s.port, f"the {s.name} service") for s in listed if s.port]
        for port, what in (*ports, (cfg.pg_port, "the database")):
            if port_in_use(port):
                raise NativeError(
                    f"Port {port}, which {what} needs, is already in use. Stop whatever holds it "
                    "(lsof -nP -iTCP:" + str(port) + " -sTCP:LISTEN), or change the port in .env."
                )
        if instances:  # Allowed beside Docker: only ever with Chromes of its own.
            for url in filter(None, (cfg.worker_cdp, cfg.twin_cdp)):
                if http_ok(url + "/json/version"):
                    raise NativeError(f"{url} already answers and Docker is running: that Chrome is not ours to drive.")
        if not cfg.worker_cdp and not system_chrome():
            self.say("No worker Chrome is configured: applications use a headless browser, which many sites distrust.")

    # ---- the loop ----------------------------------------------------------------------------------------------------

    def main(self):
        lock = os.open(self.cfg.home / "supervisor.lock", os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.say("Another supervisor is running for this data directory.")
            return 1
        self.raise_file_limit()
        previous = read_state(self.cfg)
        self.write_state(force=True)
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, self.on_stop)
        signal.signal(signal.SIGHUP, self.on_restart)
        try:
            stop_leftovers(self.cfg, previous, say=self.say)
            self.preflight()
            self.children = self.children or self.plan()
            self.write_state(force=True)
            while not self.stop_reason:
                self.tick(time.monotonic())
                time.sleep(1.0)
        except NativeError as exc:
            self.failure = self.stop_reason = str(exc)
            self.say(f"Not starting: {exc}")
        finally:
            self.shutdown()
        return 1 if self.failure else 0

    def raise_file_limit(self):
        """macOS gives a process 256 descriptors. Thirty database connections, a browser's pipes and every open HTTP
        client run through that at once; Docker's default is a million."""
        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        for want in NOFILE:
            limit = want if hard == resource.RLIM_INFINITY else min(want, hard)
            if limit <= soft:
                return
            try:
                resource.setrlimit(resource.RLIMIT_NOFILE, (limit, hard))
                return
            except (ValueError, OSError):
                continue

    def on_stop(self, *_):
        self.stop_reason = self.stop_reason or "Stopped by request."

    def on_restart(self, *_):
        self.restart_requested = True

    def apply_restart_request(self, now):
        """`axiom restart <names>` and a deploy: the names are in a file, because a signal carries none. No names: every
        service. A service that had finished or been given up on gets a fresh start."""
        request = self.cfg.home / "restart-request"
        try:
            names = json.loads(request.read_text())
            request.unlink()
        except (OSError, ValueError):
            names = []
        targets = [c for c in self.children if c.name in names] if names else [c for c in self.children if c.service]
        for child in targets:
            self.say(f"Restarting {child.name} (requested).")
            if child.proc:
                child.request_stop(now=now)
            elif child.state in {"done", "failed"}:
                child.state, child.strikes, child.next_start, child.message = "down", 0, now, ""

    def child(self, name):
        return next((c for c in self.children or [] if c.name == name), None)

    def tick(self, now):
        if self.restart_requested:
            self.restart_requested = False
            self.apply_restart_request(now)
        for child in self.children:
            self.tick_child(child, now)
        self.tick_phase(now)
        if self.due("sleep", now, ACTIVE_SECONDS):
            self.tick_sleep(now)
        if self.due("power", now, POWER_SECONDS):
            self.tick_power()
        self.tick_health(now)
        if self.due("rotate", now, ROTATE_SECONDS):
            self.rotate_logs()
        self.write_state()

    def due(self, name, now, every):
        if now < self.next_tick.get(name, 0.0):
            return False
        self.next_tick[name] = now + every
        return True

    def tick_child(self, child, now):
        if child.proc is not None and child.proc.poll() is not None:
            self.on_exit(child, now)
        if child.stopping and child.kill_at and now >= child.kill_at:
            self.say(f"{child.name} did not stop within {child.grace:.0f} s; sending signal {int(child.final_signal)}.")
            child.signal(child.final_signal)
            child.kill_at = now + 10
        if child.state == "starting" and (child.ready is None or child.ready()):
            child.state = "up"
            self.timings.setdefault(child.name + "_ready_seconds", round(now - self.began, 1))
            self.say(f"{child.name} is up (pid {child.pid}).")
            if child.name == "postgres":
                pg.ensure_database(self.pg_bin, self.cfg)
            self.restart_dependents(child, now)
        if child.state == "external" and child.url and self.due("external:" + child.name, now, HEALTH_SECONDS):
            if not http_ok(child.url + "/json/version"):
                self.say(f"The {child.name} Chrome we did not start has gone; starting our own.")
                child.state, child.next_start = "down", now
        if child.state == "down" and now >= child.next_start and self.ready_for(child, now):
            try:
                child.spawn()
            except (NativeError, OSError) as exc:
                self.on_spawn_failure(child, str(exc), now)

    def ready_for(self, child, now):
        """A service waits for what it names in `after`, and for a Chrome only so long: a hung one is the app's
        browser-outage gate's to report, and must not keep the dashboard down as well."""
        return all(
            dep.state in {"up", "external", "failed", "done"}
            or (dep.state == "starting" and dep.url and now - dep.started > CHROME_WAIT)
            for dep in child.after
        )

    def restart_dependents(self, child, now):
        """`restart_with`, once `child` is back and not before: the app's drain has to be able to read the database."""
        if not child.restarts:
            return
        for dependent in self.children:
            if child.name in dependent.restart_with and dependent.proc:
                self.say(f"{child.name} restarted; restarting {dependent.name}, which depends on it.")
                dependent.request_stop(now=now)

    def on_spawn_failure(self, child, message, now):
        child.strikes += 1
        child.message = message
        if child.strikes >= MAX_STRIKES:
            self.give_up(child, message)
        else:
            child.next_start = now + min(2 ** (child.strikes - 1), 30)
            self.say(f"{child.name} could not start: {message}")

    def on_exit(self, child, now):
        pid, code, uptime = child.proc.pid, child.proc.returncode, now - child.started
        child.reap_group(pid)
        requested = child.stopping
        child.proc, child.stopping, child.kill_at, child.lstart = None, False, None, ""
        child.state = "down"
        if self.stop_reason:
            return
        if requested:
            child.restarts += 1
            child.next_start = now
            return
        self.say(f"{child.name} exited with status {code} after {uptime:.0f} s.")
        if code == 0 and child.restart != "always":
            child.state = "done"
            return
        if child.restart == "never":
            self.give_up(child, f"it exited with status {code}, and its restart policy is never")
            return
        child.restarts += 1
        # After a long run an exit is not a crash loop, whatever its status. `POST /api/system/restart` exits 0 and relied
        # on Docker's restart policy.
        child.strikes = 0 if uptime >= STABLE_SECONDS else child.strikes + 1
        if child.strikes >= MAX_STRIKES:
            self.give_up(child, f"it exited {child.strikes} times in a row within {STABLE_SECONDS:.0f} s (status {code})")
            return
        child.next_start = now + (min(2 ** (child.strikes - 1), 30) if child.strikes else 0)

    def give_up(self, child, why):
        where = f" See {child.log}." if child.log else ""
        if child.url:
            where = " If another Chrome is using its profile, quit that one."
        message = f"{child.name} is not running: {why}.{where}"
        child.state, child.message = "failed", message
        self.say(message)
        if child.required:
            self.failure = self.stop_reason = message

    def tick_phase(self, now):
        """Up means every service is: there is one thing to open, and it is ready when all of it is."""
        listed = [c for c in self.children if c.service]
        settled = {"up", "done"}
        if self.phase == "starting" and listed and all(
            c.state in settled or (c.state == "failed" and not c.required) for c in listed
        ):
            self.phase = "running"
            self.timings["ready_seconds"] = round(now - self.began, 1)
            self.say(f"AXIOM is up at {self.cfg.base_url} after {self.timings['ready_seconds']} s.")

    # ---- the Mac: sleep, power, health, logs ----------------------------------------------------------------------------

    def tick_sleep(self, now):
        """Prevent idle sleep while applications run: the Mac sleeping froze runs again and again (2026-10-19, 10-29). -w
        ties caffeinate to this process, so it cannot outlive a crash; -i also holds on battery, -s would not."""
        database = self.child("postgres")
        count = None
        if database and database.state == "up":
            try:
                raw = pg.psql(self.pg_bin, self.cfg, ACTIVE_RUNS, timeout=5, check=False)
                count = int(raw) if raw not in (None, "") else None
            except (OSError, ValueError, subprocess.TimeoutExpired):
                count = None
        self.active = count
        if count:
            self.last_active = now
        running = bool(self.caffeinate and self.caffeinate.poll() is None)
        if count is None:
            want = running  # Unknown: keep what we had, rather than sleep under an application.
        else:
            want = self.last_active is not None and now - self.last_active < SLEEP_RELEASE_SECONDS
        if want and not running and shutil.which("caffeinate"):
            self.caffeinate = subprocess.Popen(
                ["caffeinate", "-i", "-m", "-w", str(os.getpid())], stdin=subprocess.DEVNULL, start_new_session=True
            )
            self.say(f"Preventing idle sleep: {count} application(s) active.")
        elif running and not want:
            self.caffeinate.terminate()
            self.caffeinate.wait()
            self.caffeinate = None
            self.say("Nothing active for a while: idle sleep is allowed again.")

    def tick_power(self):
        power = power_status()
        if power.get("battery") and not self.power.get("battery"):
            self.say(f"Running on battery ({power.get('percent')}%). The Mac sleeps when the lid closes or it runs out.")
        elif self.power.get("battery") and power and not power.get("battery"):
            self.say("Back on power.")
        self.power = power

    def tick_health(self, now):
        """Reported, not acted on, as under Compose. /healthz can be slow while the app is busy, and a restart in the
        middle of an application costs more than a slow answer (2026-10-15). At most one probe a tick, so that several
        slow services cannot hold up the loop that notices a process has died."""
        for child in self.children:
            if not child.health_url:
                continue
            if child.state != "up":
                child.healthy, child.healthy_since = None, None
            elif self.due("health:" + child.name, now, HEALTH_SECONDS):
                ok = http_ok(child.health_url)
                if ok != child.healthy:
                    child.healthy, child.healthy_since = ok, time.time()
                return

    def rotate_logs(self):
        logs = [c.log for c in self.children or [] if c.log]
        for path in (*logs, self.cfg.logs / "supervisor.log"):
            name = path.name
            try:
                if path.stat().st_size < LOG_BYTES:
                    continue
                for n in range(LOG_KEEP - 1, 0, -1):
                    older = path.with_name(f"{name}.{n}")
                    if older.exists():
                        older.replace(path.with_name(f"{name}.{n + 1}"))
                shutil.copyfile(path, path.with_name(f"{name}.1"))
                os.truncate(path, 0)  # The child holds an O_APPEND descriptor, which simply carries on from zero.
            except OSError:
                continue

    # ---- stopping --------------------------------------------------------------------------------------------------

    def shutdown(self):
        """The order is the point: each service drains while what it depends on still serves the commits under way, so the
        app stops before Chrome and the database."""
        self.phase, self.reason = "stopping", self.stop_reason
        self.write_state(force=True)
        for child in reversed(in_dependency_order(self.children or [])):
            if child.proc:
                if child.service and child.grace > 30:
                    self.say(f"Stopping {child.name}: it drains for up to {child.grace:.0f} s while commits finish.")
                self.stop_and_wait(child)
        if self.caffeinate and self.caffeinate.poll() is None:
            self.caffeinate.terminate()
            self.caffeinate.wait()
        self.phase = "stopped"
        self.write_state(force=True)
        self.say(self.reason or "Stopped.")

    def stop_and_wait(self, child):
        pid = child.pid
        child.stopping = False
        child.request_stop(now=time.monotonic())
        final = False
        while child.proc.poll() is None:
            now = time.monotonic()
            if now >= child.kill_at:
                if final:
                    self.say(f"{child.name} (pid {pid}) is still running; killing it.")
                    child.signal(signal.SIGKILL)
                    child.proc.wait()
                    break
                self.say(f"{child.name} did not stop within {child.grace:.0f} s; sending signal {int(child.final_signal)}.")
                child.signal(child.final_signal)
                child.kill_at = now + 10
                final = True
            self.write_state()
            time.sleep(0.25)
        child.reap_group(pid)
        child.proc, child.state, child.stopping, child.lstart = None, "down", False, ""

    # ---- state ------------------------------------------------------------------------------------------------------

    def write_state(self, force=False):
        now = time.monotonic()
        if not force and now - self.written < 5:
            return
        self.written = now
        state = {
            "pid": os.getpid(),
            "lstart": self.lstart,
            "phase": self.phase,
            "reason": self.reason or self.failure,
            "base_url": self.cfg.base_url,
            "began_at": self.began_at,
            "timings": self.timings,
            "active": self.active,
            "sleep_prevented": bool(self.caffeinate and self.caffeinate.poll() is None),
            "power": self.power,
            "stop_order": [c.name for c in reversed(in_dependency_order(self.children or []))],
            "release": str(release.current(self.cfg) or ""),
            "children": {
                c.name: {
                    "pid": c.pid,
                    "lstart": c.lstart,
                    "state": c.state,
                    "restarts": c.restarts,
                    "message": c.message,
                    "url": c.url or c.health_url,
                    "service": c.service,
                    "healthy": c.healthy,
                    "healthy_since": c.healthy_since,
                    "grace": c.grace,
                    "stop_signal": int(c.stop_signal),
                }
                for c in self.children or []
            },
        }
        temporary = self.cfg.state_file.with_suffix(".tmp")
        temporary.write_text(json.dumps(state))
        temporary.chmod(0o600)
        temporary.replace(self.cfg.state_file)


def read_state(cfg):
    try:
        return json.loads(cfg.state_file.read_text())
    except (OSError, ValueError):
        return {}


def supervisor_alive(state):
    return same_process(state.get("pid"), state.get("lstart"))


def stop_budget(state, default):
    """How long a whole stop may take: one child's grace after another, and the ten seconds each may take to die after."""
    graces = [c.get("grace") or 0 for c in (state.get("children") or {}).values() if c.get("pid")]
    return sum(graces) + 12 * len(graces) if graces else default


def stop_leftovers(cfg, state, say=print):
    """Stop what a dead supervisor left running, in the same order as a normal stop. Without this, a killed supervisor
    would leave an unsupervised app and a database on its port, and `axiom start` could only fail."""
    children = (state or {}).get("children") or {}
    found = False
    for name in (state or {}).get("stop_order") or list(children):
        entry = children.get(name) or {}
        if not same_process(entry.get("pid"), entry.get("lstart")):
            continue
        found = True
        say(f"Stopping the {name} left by an earlier supervisor (pid {entry['pid']}).")
        stop_process(
            entry["pid"], entry.get("stop_signal") or signal.SIGTERM, entry.get("grace") or cfg.stop_grace, entry["lstart"]
        )
    return stop_stray_postgres(cfg, say) or found


def stop_stray_postgres(cfg, say):
    """A cluster whose postmaster the state file does not know. PostgreSQL's own lock file names it."""
    lock = cfg.pgdata / "postmaster.pid"
    try:
        pid = int(lock.read_text().splitlines()[0])
    except (OSError, ValueError, IndexError):
        return False
    started = process_start(pid)
    command = subprocess.run(["ps", "-o", "command=", "-p", str(pid)], capture_output=True, text=True, timeout=5).stdout
    if not started or str(cfg.pgdata) not in command:
        return False  # A stale lock file, whose pid may by now be anything. PostgreSQL deals with it.
    say(f"Stopping a database left running on this data directory (pid {pid}).")
    stop_process(pid, signal.SIGINT, POSTGRES_GRACE, started)
    return True


def stop_process(pid, sig, grace, started):
    try:
        os.kill(pid, sig)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline:
        if not same_process(pid, started):
            break
        time.sleep(0.5)
    else:
        os.kill(pid, signal.SIGKILL if sig != signal.SIGINT else signal.SIGQUIT)
        time.sleep(1)
    try:
        os.killpg(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def run():
    """The supervisor process itself (`axiom start` starts it in the background)."""
    cfg = prepare(load())
    return Supervisor(cfg).main()
