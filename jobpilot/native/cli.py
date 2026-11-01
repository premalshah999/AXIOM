"""`axiom`: the native app's front door (docs/NATIVE.md). `make start`, `make stop` and `make status` call it."""

import argparse
import fcntl
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

from ..storage.mode import current as database_choice
from . import backups, migrate, pg, release, services, supervisor
from .env import NativeError, load, prepare, system_chrome
from .supervisor import read_state, same_process, stop_leftovers, supervisor_alive

STARTUP_WAIT_SECONDS = 600
ACTIVE_WAIT_SECONDS = 1800


def human_kb(kb):
    return f"{kb / 1024 / 1024:.2f} GB" if kb >= 1024 * 1024 else f"{kb / 1024:.0f} MB"


def memory_by_tree(roots):
    """{root pid: (kB resident in it and its descendants, process count)}. PostgreSQL's backends leave its process group,
    so this follows parents rather than groups. Pages shared between processes count once per process."""
    out = subprocess.run(["ps", "-axo", "pid=,ppid=,rss="], capture_output=True, text=True, timeout=5).stdout
    parent, rss = {}, {}
    for line in out.splitlines():
        fields = line.split()
        if len(fields) == 3:
            parent[int(fields[0])], rss[int(fields[0])] = int(fields[1]), int(fields[2])
    totals = {root: [0, 0] for root in roots}
    for pid in rss:
        probe = pid
        while probe and probe not in totals and probe in parent and probe != parent[probe]:
            probe = parent[probe]
        if probe in totals:
            totals[probe][0] += rss[pid]
            totals[probe][1] += 1
    return totals


def cmd_status(cfg, args):
    state = read_state(cfg)
    alive = supervisor_alive(state)
    if args.running:
        return 0 if alive else 1
    children = state.get("children") or {}
    pids = [c["pid"] for c in children.values() if c.get("pid")]
    memory = memory_by_tree(pids + ([state["pid"]] if alive else [])) if alive or pids else {}
    power = supervisor.power_status()
    if args.json:
        print(json.dumps({**state, "alive": alive, "power": power}, indent=1))
        return 0 if alive else 1
    orphans = [n for n, c in children.items() if same_process(c.get("pid"), c.get("lstart"))]
    if not alive:
        print(f"AXIOM is stopped. {state.get('reason') or ''}".rstrip())
        if orphans:
            print(f"The supervisor died and left these running: {', '.join(orphans)}. Run: axiom stop")
        _print_warnings(cfg, power, state)
        return 1
    print(f"AXIOM ({state.get('phase')})  {state.get('base_url')}")
    release_name = os.path.basename(state.get("release") or "")
    timings = state.get("timings") or {}
    print(f"  release     {release_name}" + (f"  (up in {timings['ready_seconds']} s)" if "ready_seconds" in timings else ""))
    total = 0
    rows = [("supervisor", {"pid": state["pid"], "state": "up", "restarts": 0}), *children.items()]
    for name, child in rows:
        kb, count = memory.get(child.get("pid"), (0, 0))
        total += kb
        size = f"{human_kb(kb)} in {count} process{'es' if count != 1 else ''}" if child.get("pid") else ""
        where = child.get("url") or ""
        restarts = f"restarted {child['restarts']}x" if child.get("restarts") else ""
        line = f"  {name:<11} {child.get('state', '?'):<9} {size:<26} {where:<24} {restarts}"
        print(line.rstrip())
        if child.get("message"):
            print(f"              {child['message']}")
    print(f"  memory      {human_kb(total)} resident (pages shared between processes count once per process)")
    for name, child in children.items():
        if child.get("healthy") is False:
            since = time.strftime("%H:%M:%S", time.localtime(child.get("healthy_since") or time.time()))
            print(f"  health      {name} is not answering its health check (since {since})")
    hold = _hold_stage(cfg)
    if hold and hold[0] in {"draining", "canary"}:
        print(f"  HOLD        Nothing new starts ({hold[1] or hold[0]}). Release it with: axiom hold off")
    active = state.get("active")
    sleep = "prevented" if state.get("sleep_prevented") else "allowed"
    print(f"  sleep       idle sleep {sleep}; {active if active is not None else '?'} application(s) active")
    _print_warnings(cfg, power, state)
    return 0


def _hold_stage(cfg):
    """A forgotten hold stops all intake silently, so the one place everyone looks must show it."""
    try:
        raw = pg.psql(
            pg.find_bin(cfg),
            cfg,
            "select value->>'stage', value->>'reason' from settings where key='execution_v2_deployment_hold'",
            timeout=5,
            check=False,
        )
    except (NativeError, OSError, subprocess.TimeoutExpired):
        return None
    return raw.split("|", 1) if raw else None


def _print_warnings(cfg, power, state):
    if power.get("battery"):
        print(
            f"  WARNING     On battery ({power.get('percent')}%, {power.get('remaining') or 'unknown'} left). Plug in: the Mac "
            "still sleeps when the lid closes or the battery runs out, and that freezes applications."
        )
    if cfg.data_dir.exists():
        free = shutil.disk_usage(cfg.data_dir).free
        print(f"  disk        {free / migrate.GIB:.1f} GB free")
    if database_choice(cfg.values, cfg.data_dir) == "local":
        print(f"  backup      {backups.summary(cfg)}")
        if free < 3 * migrate.GIB:
            print("  WARNING     Under 3 GB free: runs save pages and screenshots, and the database needs room to write.")


def cmd_start(cfg, args):
    if not (cfg.root / ".env").exists():  # A fresh clone: the same generated .env that `make up` makes.
        subprocess.run(["sh", str(cfg.root / "scripts" / "setup.sh"), "--env-only"], check=True)
        cfg = load(cfg.root)
    prepare(cfg)
    state = read_state(cfg)
    if supervisor_alive(state):
        print(f"AXIOM is already running at {state.get('base_url')}.")
        _open(cfg, args)
        return 0
    if database_choice(cfg.values, cfg.data_dir) == "local":
        pg.find_bin(cfg)  # Fail here, in the terminal, rather than in a log.
    services.read(cfg, release.ensure(cfg))
    began = time.monotonic()
    log = os.open(cfg.logs / "supervisor.log", os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    process = subprocess.Popen(
        [sys.executable, "-m", "jobpilot.native", "_supervise"],
        cwd=cfg.root,
        stdin=subprocess.DEVNULL,
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    os.close(log)
    print("Starting AXIOM ...")
    seen = set()
    deadline = time.monotonic() + STARTUP_WAIT_SECONDS
    while time.monotonic() < deadline:
        time.sleep(0.5)
        state = read_state(cfg)
        ours = state.get("pid") == process.pid
        for name, child in (state.get("children") or {}).items() if ours else ():
            if child.get("state") in {"up", "external"} and name not in seen:
                seen.add(name)
                print(f"  {name} {'is already running, left alone' if child['state'] == 'external' else 'is up'}")
        if ours and state.get("phase") == "running":
            print(f"AXIOM is up at {cfg.base_url} after {time.monotonic() - began:.1f} s. Stop it with: axiom stop")
            _warn_power()
            _open(cfg, args)
            return 0
        if process.poll() is not None or (ours and state.get("phase") == "stopped"):
            reason = (read_state(cfg).get("reason") if ours else "") or "The supervisor exited."
            print(f"AXIOM did not start: {reason}", file=sys.stderr)
            print(f"Logs: axiom logs supervisor ({cfg.logs})", file=sys.stderr)
            return 1
    print(f"AXIOM is still not up after {STARTUP_WAIT_SECONDS} s. See: axiom status, axiom logs", file=sys.stderr)
    return 1


def _warn_power():
    power = supervisor.power_status()
    if power.get("battery"):
        print(f"WARNING: on battery ({power.get('percent')}%). Plug in before leaving applications running.")


def _open(cfg, args):
    if not args.no_open and shutil.which("open"):
        subprocess.run(["open", cfg.base_url], check=False)


def cmd_stop(cfg, args):
    state = read_state(cfg)
    if not supervisor_alive(state):
        if stop_leftovers(cfg, state):
            print("Stopped what an earlier supervisor left running.")
        else:
            print("AXIOM is not running.")
        return 0
    os.kill(state["pid"], signal.SIGTERM)
    print(f"Stopping AXIOM: services drain for up to {cfg.stop_grace} s while commits finish ...")
    deadline = time.monotonic() + supervisor.stop_budget(state, cfg.stop_grace) + 60
    while time.monotonic() < deadline:
        if not supervisor_alive(read_state(cfg)):
            print("Stopped.")
            return 0
        time.sleep(0.5)
    print("It has not stopped yet. See: axiom logs supervisor", file=sys.stderr)
    return 1


def cmd_restart(cfg, args):
    if args.names:
        state = read_state(cfg)
        if not supervisor_alive(state):
            print("AXIOM is not running.", file=sys.stderr)
            return 1
        known = sorted(state.get("children") or {})
        unknown = [n for n in args.names if n not in known]
        if unknown:
            print(f"axiom: nothing is called {', '.join(unknown)}. Running here: {', '.join(known)}", file=sys.stderr)
            return 1
        return restart_children(cfg, state, args.names)
    cmd_stop(cfg, args)
    return cmd_start(cfg, args)


def restart_children(cfg, state, names=()):
    """Restart these children, or with no names every service, and wait until each is back and healthy."""
    children = state.get("children") or {}
    names = list(names) or [n for n, c in children.items() if c.get("service")]
    old = {n: children[n].get("pid") for n in names}
    request = cfg.home / "restart-request"
    request.write_text(json.dumps(names))
    request.chmod(0o600)
    os.kill(state["pid"], signal.SIGHUP)
    print(f"Restarting {', '.join(names)} ...")
    deadline = time.monotonic() + supervisor.stop_budget(state, cfg.stop_grace) + STARTUP_WAIT_SECONDS
    while time.monotonic() < deadline:
        time.sleep(1)
        now = read_state(cfg).get("children") or {}
        back = [
            n
            for n in names
            if now.get(n, {}).get("state") == "done"
            or (now.get(n, {}).get("state") == "up" and now[n].get("pid") not in (None, old[n]))
        ]
        if len(back) == len(names):
            print(f"{', '.join(names)} {'is' if len(names) == 1 else 'are'} back.")
            return 0
    print(f"{', '.join(names)} did not come back. See: axiom status, axiom logs", file=sys.stderr)
    return 1


def cmd_logs(cfg, args):
    name = args.name or services.read(cfg, release.current(cfg))[0].name
    path = cfg.logs / f"{name}.log"
    if not path.exists():
        found = sorted(p.stem for p in cfg.logs.glob("*.log")) if cfg.logs.exists() else []
        print(f"No log yet at {path}. Logs here: {', '.join(found) or 'none'}", file=sys.stderr)
        return 1
    os.execvp("tail", ["tail", "-n", str(args.lines), *(["-f"] if args.follow else []), str(path)])


def cmd_psql(cfg, args):
    bin_dir = pg.find_bin(cfg)
    remote = pg.remote(cfg)  # Supabase or a server: the connection is in libpq's environment, so no password is on a command line.
    os.execve(
        bin_dir / "psql",
        [str(bin_dir / "psql"), *([] if remote else pg.connection(cfg)), *args.rest],  # A full argv[0]: psql finds its files from it.
        {**os.environ, "PGPASSWORD": cfg.password, "PGTZ": "UTC", **(remote or {})},
    )


def cmd_backup(cfg, args):
    """A copy of the database outside the data folder: `axiom backup [file]`. pg_dump reads one consistent snapshot, so the
    app keeps running. The file is written beside its name and renamed once pg_restore has read it back."""
    auto = getattr(args, "auto", False)
    if auto and (problem := backups.room_for_one()):
        backups.record(cfg, skipped=problem)
        print("Skipped the automatic backup: " + problem)
        return 0
    try:
        target = dump_database(cfg, Path(args.file).expanduser() if args.file else None, auto=auto)
    except NativeError as exc:
        if auto:
            backups.record(cfg, error=str(exc)[-300:])
        raise
    if auto:
        backups.record(cfg, file=target.name)
        backups.prune()
    return 0


def dump_database(cfg, target=None, *, auto=False, label="axiom-"):
    """One readable copy of the database at `target`, or a new file in ~/axiom-backups; returns its path."""
    bin_dir = pg.find_bin(cfg)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    target = target or backups.folder() / (backups.next_name() if auto else f"{label}{stamp}.dump")
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    partial = target.with_name(target.name + ".partial")
    remote = pg.remote(cfg)
    env = pg._client_env(cfg)
    try:
        with open(os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "wb") as out:
            done = subprocess.run(
                [str(bin_dir / "pg_dump"), *([] if remote else pg.connection(cfg)), "-Fc", "-Z", "6"],
                stdout=out, stderr=subprocess.PIPE, env=env, check=False,
            )
        if done.returncode:
            raise NativeError("pg_dump: " + done.stderr.decode(errors="replace").strip()[-400:])
        listed = subprocess.run(
            [str(bin_dir / "pg_restore"), "-l", str(partial)], capture_output=True, text=True, env=env, check=False
        )
        tables = sum(" TABLE DATA " in line for line in listed.stdout.splitlines())
        if listed.returncode or not tables:
            raise NativeError("The backup could not be read back: " + listed.stderr.strip()[-400:])
        os.replace(partial, target)
    finally:
        partial.unlink(missing_ok=True)
    print(f"{target}  {target.stat().st_size / 1e9:.2f} GB, {tables} tables")
    return target


def cmd_restore(cfg, args):
    """Put a backup back: `axiom restore <file>`. AXIOM must be stopped. What the database holds now is copied first, so a
    wrong choice of file can itself be undone. pg_restore's own --clean replaces each object; run once on a scratch cluster
    (2026-11-01), it brought back every row, with the workflow tables, through a full stop and start of PostgreSQL."""
    source = Path(args.file).expanduser()
    if not source.is_file():
        raise NativeError(f"No backup at {source}.")
    if database_choice(cfg.values, cfg.data_dir) != "local":
        raise NativeError("Only the database on this computer can be restored here. Set DATABASE_MODE=local first.")
    if not cfg.password:
        raise NativeError("POSTGRES_PASSWORD is not set in .env, so the database cannot be opened.")
    if not args.yes:
        answer = input(f"Replace everything in the database with {source.name}? Type yes: ") if sys.stdin.isatty() else ""
        if answer.strip().casefold() != "yes":
            print("Nothing was changed. To skip this question: axiom restore --yes <file>")
            return 1
    with MigrationServer(cfg) as server:
        env = pg._client_env(cfg)
        listed = subprocess.run(
            [str(server.bin_dir / "pg_restore"), "-l", str(source)], capture_output=True, text=True, env=env, check=False
        )
        if listed.returncode or " TABLE DATA " not in listed.stdout:
            raise NativeError(f"{source.name} is not a readable database backup: " + listed.stderr.strip()[-300:])
        before = dump_database(cfg, label="axiom-before-restore-")
        print(f"What the database held before this is kept at {before}.")
        done = subprocess.run(
            [str(server.bin_dir / "pg_restore"), *pg.connection(cfg), "--clean", "--if-exists", "--no-owner", "--no-privileges",
             "--single-transaction", "--exit-on-error", str(source)],
            capture_output=True, text=True, env=env, check=False,
        )
        if done.returncode:
            raise NativeError(f"The restore failed and changed nothing: {done.stderr.strip()[-400:]}")
    print("Restored. Start AXIOM again.")
    return 0


def cmd_jobpilot(cfg, args):
    """The app's own commands, with the environment the running app has: `axiom jobpilot token`, `... verify`."""
    found = release.ensure(prepare(cfg))
    os.chdir(cfg.root)
    env = cfg.app_environment(found)
    os.execve(sys.executable, [sys.executable, "-m", "jobpilot.cli", *args.rest], env)


def cmd_hold(cfg, args):
    bin_dir = pg.find_bin(cfg)
    if args.state == "on":
        migrate.set_hold(bin_dir, cfg, "draining", "held with axiom hold")
        print("Holding: nothing new starts until `axiom hold off`.")
    else:
        migrate.set_hold(bin_dir, cfg, "complete", "released with axiom hold")
        print("Released: applications can start.")
    return 0


def cmd_deploy(cfg, args):
    """scripts/deploy.sh for this mode. Snapshot first, so that the wait is the only downtime; then the same order: hold,
    wait for nothing active, swap, health, release. The hold is always released, so dispatch never stays stopped."""
    state = read_state(cfg)
    if not supervisor_alive(state):
        raise NativeError("AXIOM is not running. Start it with: axiom start")
    bin_dir = pg.find_bin(cfg)
    new = release.make(cfg)
    migrate.set_hold(bin_dir, cfg, "draining", f"deploy {new.name}")
    try:
        waited = 0
        while (active := pg.psql(bin_dir, cfg, supervisor.ACTIVE_RUNS)) != "0":
            if not args.wait or waited >= ACTIVE_WAIT_SECONDS:
                print(f"deploy: {active} application(s) still active; not deploying (use --wait to wait)", file=sys.stderr)
                return 1
            time.sleep(15)
            waited += 15
        previous = release.current(cfg) or new
        lock_changed = _lock_digest(new) != _lock_digest(previous)
        # A changed list of services is a new process tree, which the running supervisor was not started with.
        list_changed = services.source_file(cfg, new).read_bytes() != services.source_file(cfg, previous).read_bytes()
        services.read(cfg, new)  # A mistake in the new list must not cost the running installation anything.
        release.activate(cfg, new)
        args.no_open = True
        if lock_changed:
            # Dependencies changed: a process must not be alive while its site-packages are rewritten.
            cmd_stop(cfg, args)
            subprocess.run(["uv", "sync", "--frozen", "--inexact", "--no-dev"], cwd=cfg.root, check=True,
                           env={**os.environ, "UV_PROJECT_ENVIRONMENT": sys.prefix})
            code = cmd_start(cfg, args)
        elif list_changed:
            cmd_stop(cfg, args)
            code = cmd_start(cfg, args)
        else:
            code = restart_children(cfg, read_state(cfg))
        if code == 0:
            print(f"deploy: {new.name} is healthy")
        return code
    finally:
        migrate.set_hold(bin_dir, cfg, "complete", f"deploy {new.name}")
        print("deploy: hold released")


def _lock_digest(folder):
    path = folder / "uv.lock"
    return path.read_bytes() if path.exists() else None


def cmd_setup(cfg, args):
    """Everything `axiom start` needs, done once. Re-running is safe."""
    if not (cfg.root / ".env").exists():
        subprocess.run(["sh", str(cfg.root / "scripts" / "setup.sh"), "--env-only"], check=True)
        cfg = load()
    prepare(cfg)
    try:
        bin_dir = pg.find_bin(cfg)
    except NativeError:
        if database_choice(cfg.values, cfg.data_dir) != "local":
            print("Database    not on this computer: PostgreSQL is not needed here (only psql, for `axiom psql`)")
            bin_dir = None
        elif not shutil.which("brew"):
            raise
        else:
            print("Installing PostgreSQL 17 with Homebrew (about 80 MB, plus its libraries) ...")
            subprocess.run(["brew", "install", f"postgresql@{pg.MAJOR}"], check=True)
            bin_dir = pg.find_bin(cfg)
    if bin_dir:
        print(f"PostgreSQL  {bin_dir}")
    print(f"Chrome      {system_chrome() or 'not found: install Google Chrome for real job sites'}")
    latex = shutil.which("pdflatex", path=cfg.path_with_tools())
    print(f"pdflatex    {latex or 'not found: resume tailoring needs it (brew install --cask basictex)'}")
    found = release.ensure(cfg)
    print(f"Release     {found.name}")
    print(f"Services    {', '.join(s.name for s in services.read(cfg, found))}")
    print(f"Data        {cfg.data_dir}")
    print("Ready. Start it with: scripts/axiom start")
    return 0


class MigrationServer:
    """The native database, up for the length of a restore, with no app on it. The supervisor's lock is held, so that
    `axiom start` cannot race the restore."""

    def __init__(self, cfg):
        self.cfg, self.process, self.lock = cfg, None, None

    def __enter__(self):
        cfg = prepare(self.cfg)
        self.lock = os.open(cfg.home / "supervisor.lock", os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise NativeError("AXIOM is running here. Stop it before restoring a database: axiom stop") from None
        self.bin_dir = pg.find_bin(cfg)
        pg.init_cluster(self.bin_dir, cfg)
        supervisor.stop_stray_postgres(cfg, print)  # A restore killed part way leaves its server behind.
        if supervisor.port_in_use(cfg.pg_port):
            raise NativeError(f"Port {cfg.pg_port}, which the native database uses, is already in use.")
        log = os.open(cfg.logs / "postgres.log", os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        self.process = subprocess.Popen(
            pg.server_argv(self.bin_dir, cfg),
            env=pg.server_env(),
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        os.close(log)
        deadline = time.monotonic() + 60
        while not pg.is_ready(self.bin_dir, cfg):
            if time.monotonic() > deadline or self.process.poll() is not None:
                raise NativeError(f"The native database did not start. See {cfg.logs / 'postgres.log'}")
            time.sleep(0.5)
        pg.ensure_database(self.bin_dir, cfg)
        return self

    def __exit__(self, *_):
        if self.process and self.process.poll() is None:
            self.process.send_signal(signal.SIGINT)
            try:
                self.process.wait(timeout=60)
            except subprocess.TimeoutExpired:
                self.process.send_signal(signal.SIGQUIT)
                self.process.wait()
        if self.lock is not None:
            os.close(self.lock)


def cmd_migrate(cfg, args):
    if database_choice(cfg.values, cfg.data_dir) != "local":
        raise NativeError("A Docker installation is restored into the database on this computer. Set DATABASE_MODE=local first.")
    if not cfg.password:
        raise NativeError("POSTGRES_PASSWORD is not set in .env. Create one with: scripts/setup.sh --env-only")
    source = migrate.source_from(cfg, args)
    if args.step == "plan":
        return migrate.plan(cfg, source, args.keep_free_gb)
    if args.step == "files":
        prepare(cfg)
        return migrate.files(cfg, source, args.keep_free_gb, args.sample)
    with MigrationServer(cfg) as server:
        return migrate.database(
            cfg,
            source,
            server.bin_dir,
            replace=args.replace,
            keep_free_gb=args.keep_free_gb,
        )


def parser():
    top = argparse.ArgumentParser(prog="axiom", description="AXIOM on this computer, without Docker (docs/NATIVE.md)")
    sub = top.add_subparsers(dest="command", required=True, metavar="command")
    sub.add_parser("setup", help="Install what is missing: PostgreSQL 17, the dashboard, .env")
    start = sub.add_parser("start", help="Start everything, wait until healthy, open the dashboard")
    start.add_argument("--no-open", action="store_true", help="Do not open the dashboard")
    sub.add_parser("stop", help="Drain and stop everything (up to 180 s)")
    status = sub.add_parser("status", help="What is running, its memory, sleep, power and disk")
    status.add_argument("--json", action="store_true")
    status.add_argument("--running", action="store_true", help="Print nothing; exit 0 when running")
    restart = sub.add_parser("restart", help="Stop and start everything, or `restart <name> ...` for those alone")
    restart.add_argument("names", nargs="*", help="Services, or postgres, worker, twin")
    restart.add_argument("--no-open", action="store_true")
    logs = sub.add_parser("logs", help="Show a log: a service's (default the first), postgres or supervisor")
    logs.add_argument("name", nargs="?")
    logs.add_argument("-n", "--lines", type=int, default=100)
    logs.add_argument("-f", "--follow", action="store_true")
    sub.add_parser("psql", help="Open psql on the native database")
    backup = sub.add_parser("backup", help="Copy the database to a file; the app keeps running")
    backup.add_argument("file", nargs="?", help="Default: ~/axiom-backups/axiom-<date>.dump")
    backup.add_argument("--auto", action="store_true", help=argparse.SUPPRESS)
    restore = sub.add_parser("restore", help="Put a backup back, once AXIOM is stopped; today's database is copied first")
    restore.add_argument("file")
    restore.add_argument("--yes", action="store_true", help="Do not ask first")
    sub.add_parser("jobpilot", help="Run an app command here, for example: axiom jobpilot token")
    hold = sub.add_parser("hold", help="Stop or allow new applications (execution_v2_deployment_hold)")
    hold.add_argument("state", choices=["on", "off"])
    deploy = sub.add_parser("deploy", help="Snapshot the checkout and restart on it, once nothing is active")
    deploy.add_argument("--wait", action="store_true", help="Wait up to 30 minutes for active applications")
    deploy.add_argument("--no-open", action="store_true", default=True, help=argparse.SUPPRESS)
    move = sub.add_parser("migrate", help="Move a Docker installation here; Docker is only read (docs/NATIVE.md)")
    move.add_argument("step", choices=["plan", "db", "files"])
    move.add_argument("--db-container", help="Default: <COMPOSE_PROJECT_NAME>-db-1")
    move.add_argument("--volume", help="Default: <COMPOSE_PROJECT_NAME>_applicant-data")
    move.add_argument("--keep-free-gb", type=float, default=migrate.DEFAULT_KEEP_FREE_GB)
    move.add_argument("--sample", type=int, default=50, help="Files to hash on both sides after copying")
    move.add_argument("--replace", action="store_true", help="db: replace a native database that already has tables")
    sub.add_parser("_supervise")  # The background process `start` launches.
    return top


COMMANDS = {
    "setup": cmd_setup,
    "start": cmd_start,
    "stop": cmd_stop,
    "status": cmd_status,
    "restart": cmd_restart,
    "logs": cmd_logs,
    "psql": cmd_psql,
    "backup": cmd_backup,
    "restore": cmd_restore,
    "jobpilot": cmd_jobpilot,
    "hold": cmd_hold,
    "deploy": cmd_deploy,
    "migrate": cmd_migrate,
}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    if argv[:1] in (["psql"], ["jobpilot"]):  # Everything after the word belongs to that program, flags included.
        args = argparse.Namespace(command=argv[0], rest=argv[1:])
    else:
        args = parser().parse_args(argv)
    try:
        if args.command == "_supervise":
            return supervisor.run()
        return COMMANDS[args.command](load(), args) or 0
    except NativeError as exc:
        print(f"axiom: {exc}", file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as exc:
        print(f"axiom: {' '.join(map(str, exc.cmd))} failed with status {exc.returncode}", file=sys.stderr)
        return 1
