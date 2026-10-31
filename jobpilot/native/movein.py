"""`axiom move-in --from <checkout>`: bring a checkout install (`.env`, `.data`) into the Mac app's folder.

The rules are migrate.py's, for the same reasons:

* **The source is only ever read.** It is what you roll back to. Nothing here deletes or writes there, and the end of the
  move proves that by comparing its listing with the one taken at the start.
* **Never fill the disk** (2026-10-21). On one volume every file is an APFS clone, so 40 GB costs almost nothing and the source
  stays independent of the copy. Free space is measured again before each batch, in case that assumption is wrong.
* **Prove it arrived.** Every file's path, size, modification time and link target match; every database file and key is
  hashed on both sides, and a sample of the rest. The bundled PostgreSQL then opens the copy and counts every table, before
  and after the only two changes the move makes to it.
* **Nothing starts until you have looked.** The moved database has a hold on, as `migrate db` leaves it, and the app refuses
  to start while the old installation runs: two installations would apply to the same jobs twice.

Run `--plan` first: it changes nothing anywhere.
"""

import argparse
import hashlib
import json
import os
import random
import shutil
import subprocess
import time
from pathlib import Path

from . import migrate, pg
from .env import NativeError, load, read_dotenv
from .supervisor import process_start, read_state, supervisor_alive

GIB = 1024**3
KEEP_FREE_BYTES = 2 * GIB
BATCH = 64  # Runs copied between looks at the free space.
SKIP_TOP = {"native"}  # The old install's pids, logs and code snapshots. The app has its own.
# Locks of processes that are not running, which would stop the new one from starting.
SKIP_NAMES = {"postmaster.pid", "SingletonLock", "SingletonCookie", "SingletonSocket"}
# Where things were on the old installation. The app decides these for itself (desktop.environment), and a stale port here
# could point at production's own.
LOCATION_KEYS = {
    "DATA_DIR", "APP_PORT", "APP_BIND", "BASE_URL", "BROWSER_CDP_URL", "BROWSER_TWIN_CDP_URL", "AXIOM_PG_PORT", "AXIOM_PG_BIN",
    "CHROME_BIN", "CHROMIUM_PATH", "AXIOM_SERVICES", "COMPOSE_PROJECT_NAME", "DATABASE_URL", "DBOS_DATABASE_URL",
}  # fmt: skip
PROFILE_KEYS = {"CHROME_WORKER_PROFILE", "CHROME_TWIN_PROFILE"}
ALWAYS_HASH = {"access-token", "mail-key", "connections.enc"}


def human(n):
    return f"{n / GIB:.1f} GB" if n >= GIB else f"{n / 1024**2:.0f} MB"


def listing(root):
    """{relative path: (kind, size, mtime_ns, link target)} of everything that moves. Directories count by their presence:
    writing into one changes its modification time."""
    entries, root = {}, Path(root)
    for folder, dirs, files in os.walk(root):
        relative = Path(folder).relative_to(root)
        if str(relative) == ".":
            dirs[:] = [d for d in dirs if d not in SKIP_TOP]
        for name in dirs:
            if name in SKIP_NAMES:
                continue
            if os.path.islink(os.path.join(folder, name)):  # os.walk does not descend it.
                entries[str(relative / name)] = ("link", 0, 0, os.readlink(os.path.join(folder, name)))
            else:
                entries[str(relative / name)] = ("dir", 0, 0, "")
        for name in files:
            if name in SKIP_NAMES:
                continue
            path = os.path.join(folder, name)
            info = os.lstat(path)
            if os.path.islink(path):
                entries[str(relative / name)] = ("link", 0, 0, os.readlink(path))
            else:
                entries[str(relative / name)] = ("file", info.st_size, info.st_mtime_ns, "")
    return entries


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def compare(source, copy, ours, theirs, sample, say=print):
    """Every difference between the source and the copy, as lines. Empty means they match."""
    problems = []
    for path in sorted(set(ours) | set(theirs)):
        if path not in ours or path not in theirs:
            problems.append(f"{path}: only in the {'copy' if path in ours else 'source'}")
        elif ours[path] != theirs[path]:
            problems.append(f"{path}: {theirs[path]} in the source, {ours[path]} in the copy")
    if problems:
        return problems
    files = [p for p, entry in theirs.items() if entry[0] == "file"]
    must = [p for p in files if p.startswith("postgres" + os.sep) or Path(p).name in ALWAYS_HASH]
    rest = [p for p in files if p not in set(must)]
    chosen = must + random.sample(rest, min(sample, len(rest)))
    say(f"Hashing {len(chosen):,} of {len(files):,} files: every database file and key, and a sample of the rest ...")
    for path in chosen:
        if _sha256(Path(source) / path) != _sha256(Path(copy) / path):
            problems.append(f"{path}: the contents differ")
    return problems


def _clone(source, target):
    """`cp -c` clones on APFS and copies where it cannot. -p keeps times and modes, -R keeps symlinks as symlinks."""
    done = subprocess.run(["/bin/cp", "-c", "-p", "-R", str(source), str(target)], capture_output=True, text=True)
    if done.returncode:
        raise NativeError(f"Copying {source} failed: {done.stderr.strip()[-300:]}")


def _check_floor(target, floor=KEEP_FREE_BYTES):
    free = shutil.disk_usage(target).free
    if free < floor:
        raise NativeError(
            f"Only {human(free)} is free, and this move stops at {human(floor)}. Nothing has been deleted: free some space and "
            "run it again."
        )


def copy_tree(source, staging, say=print, floor=KEEP_FREE_BYTES):
    """Top-level entries one at a time and `runs` one run at a time, so that the floor is checked while there is still room."""
    staging = Path(staging)
    staging.mkdir(parents=True, mode=0o700)
    os.chmod(staging, os.stat(source).st_mode & 0o777)
    for entry in sorted(os.scandir(source), key=lambda e: e.name):
        if entry.name in SKIP_TOP or entry.name in SKIP_NAMES:
            continue
        _check_floor(staging, floor)
        if entry.name == "runs" and entry.is_dir(follow_symlinks=False):
            runs = staging / "runs"
            runs.mkdir(mode=0o700)
            os.chmod(runs, os.stat(entry.path).st_mode & 0o777)
            children = sorted(os.scandir(entry.path), key=lambda e: e.name)
            for n, child in enumerate(children):
                if n % BATCH == 0:
                    _check_floor(staging, floor)
                    say(f"Runs: {n:,} of {len(children):,}")
                _clone(child.path, runs / child.name)
        else:
            say(f"Copying {entry.name} ...")
            _clone(entry.path, staging / entry.name)
    # Locks copied inside a folder: a Chrome profile's, the database's. Their absence is what lets the new processes start.
    for folder, dirs, files in os.walk(staging):
        for name in SKIP_NAMES.intersection(files) | SKIP_NAMES.intersection(dirs):
            os.unlink(os.path.join(folder, name))


def moved_env(source_cfg, source_env):
    """The old `.env`, with where-things-were commented out. The database password has to come along: the cluster was
    created with it. Values stay in this file and are never printed."""
    data = str(source_cfg.data_dir) + os.sep
    values = read_dotenv(source_env)
    lines = []
    for raw in source_env.read_text().splitlines():
        key = raw.split("=", 1)[0].removeprefix("export ").strip() if "=" in raw and not raw.lstrip().startswith("#") else ""
        inside = key in PROFILE_KEYS and (values.get(key, "") + os.sep).startswith(data)
        lines.append(f"# Moved in; the app decides this itself: {key}" if key in LOCATION_KEYS or inside else raw)
    return "\n".join(lines) + "\n"


def source_refusal(cfg):
    """What stops the source being read as a consistent whole. A copy of a live database or Chrome profile is not one."""
    if supervisor_alive(read_state(cfg)):
        return f"The installation at {cfg.root} is running. Stop it first: scripts/axiom stop"
    lock = cfg.pgdata / "postmaster.pid"
    try:
        pid = int(lock.read_text().splitlines()[0])
    except (OSError, ValueError, IndexError):
        pid = 0
    if pid and process_start(pid):
        command = subprocess.run(["ps", "-o", "command=", "-p", str(pid)], capture_output=True, text=True, timeout=5).stdout
        if str(cfg.pgdata) in command:
            return f"The database in {cfg.pgdata} is still running (pid {pid}). Stop it first: scripts/axiom stop"
    for profile in ("desktop-browser", "twin-browser"):
        try:
            owner = os.readlink(cfg.data_dir / profile / "SingletonLock").rsplit("-", 1)[1]
        except (OSError, IndexError):
            continue
        if owner.isdigit() and process_start(int(owner)):
            return f"A Chrome is still using {cfg.data_dir / profile} (pid {owner}). Quit it first: its files are changing."
    return ""


def plan(layout, cfg, say=print):
    """Whether it fits and what is in the way. Writes nothing."""
    theirs = listing(cfg.data_dir)
    size = sum(e[1] for e in theirs.values())
    target = next(p for p in (layout.support, *layout.support.parents) if p.exists())
    same_volume = os.stat(cfg.data_dir).st_dev == os.stat(target).st_dev
    free = shutil.disk_usage(target).free
    needs = KEEP_FREE_BYTES + (0 if same_volume else size)
    say(f"From        {cfg.data_dir} ({len(theirs):,} entries, {human(size)})")
    say(f"To          {layout.data}")
    say(f"How         {'APFS clones on one volume: almost no extra space' if same_volume else 'a full copy to another volume'}")
    say(f"Free        {human(free)} (needs {human(needs)})")
    refusal = source_refusal(cfg)
    if refusal:
        say(f"Blocked     {refusal}")
    if layout.data.exists() and any(layout.data.iterdir()):
        say(f"Existing    {layout.data} has data. --replace renames it aside, and never deletes it.")
    return 1 if refusal or free < needs else 0


def refresh_collation(bin_dir, cfg, say=print):
    """An ICU that differs from the one that built the indexes can order text differently, which silently breaks them. This
    is the fix PostgreSQL's own warning prescribes. Returns True if it was needed."""
    versions = pg.psql(
        bin_dir,
        cfg,
        "select coalesce(datcollversion,''), coalesce(pg_database_collation_actual_version(oid),'') "
        "from pg_database where datname=current_database()",
    )
    stored, actual = versions.split("|", 1)
    if stored == actual:
        return False
    say(f"Text sorting changed version ({stored or 'none'} to {actual or 'none'}): rebuilding the indexes ...")
    pg.psql(bin_dir, cfg, f"reindex database {pg.DATABASE}", timeout=3600)
    pg.psql(bin_dir, cfg, f"alter database {pg.DATABASE} refresh collation version")
    return True


def _set_aside(path, stamp, say):
    if path.exists():
        aside = path.with_name(f"{path.name}.before-move-{stamp}")
        path.rename(aside)
        say(f"Kept what was there as {aside}.")


def move(layout, source_root, *, replace=False, sample=200, say=print):
    """Stage, verify, swap in, open and count. Returns 0, or raises with what to do; nothing of the source is ever at risk."""
    from .cli import MigrationServer
    from .desktop import ensure_config, pick_ports, write_private

    source_root = Path(source_root).expanduser().resolve()
    source_env = source_root / ".env"
    if not source_env.exists():
        raise NativeError(f"{source_root} has no .env: that is not a checkout install.")
    cfg = load(source_root, environ={})
    if not cfg.password:
        raise NativeError(f"{source_env} has no POSTGRES_PASSWORD, which the database was created with.")
    if not (cfg.pgdata / "PG_VERSION").exists():
        raise NativeError(f"{cfg.pgdata} is not a PostgreSQL cluster.")
    if refusal := source_refusal(cfg):
        raise NativeError(refusal)
    if layout.support.exists():
        state = read_state(_destination(layout, ports=None))
        if supervisor_alive(state):
            raise NativeError("AXIOM is open. Quit it before moving data in.")
    if layout.data.exists() and any(layout.data.iterdir()) and not replace:
        raise NativeError(f"{layout.data} already has data. Add --replace to rename it aside; it is never deleted.")
    if plan(layout, cfg, say=say):
        raise NativeError("It does not fit. Nothing has been changed.")
    began = time.monotonic()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    before = listing(cfg.data_dir)
    layout.support.mkdir(parents=True, exist_ok=True, mode=0o700)
    staging = layout.support / f".moving-{stamp}"
    copy_tree(cfg.data_dir, staging, say)
    say(f"Copied in {time.monotonic() - began:.0f} s. Comparing every entry with the source ...")
    problems = compare(cfg.data_dir, staging, listing(staging), before, sample, say)
    if problems:
        raise NativeError(f"{len(problems)} difference(s), first: {problems[0]}. The copy is in {staging}, and nothing changed.")
    # The point of no return, such as it is: both of these can be renamed back, and the source has not been touched.
    _set_aside(layout.data, stamp, say)
    _set_aside(layout.env_file, stamp, say)
    staging.rename(layout.data)
    write_private(layout.env_file, moved_env(cfg, source_env))
    ensure_config(layout)
    ports = pick_ports(layout)
    new = _destination(layout, ports)
    say("Opening the copy with the bundled PostgreSQL and counting every table ...")
    with MigrationServer(new) as server:
        ask = lambda sql: pg.psql(server.bin_dir, new, sql, timeout=1800)  # noqa: E731
        counted = migrate.table_counts(ask, ask(migrate.TABLES).splitlines())
        rebuilt = refresh_collation(server.bin_dir, new, say)
        migrate.rewrite_queued_paths(server.bin_dir, new, old=str(cfg.data_dir) + os.sep)
        migrate.set_hold(
            server.bin_dir, new, "draining", "moved from a checkout: release with Settings or `axiom hold off` once it looks right"
        )
        after = migrate.table_counts(ask, ask(migrate.TABLES).splitlines())
    # The hold is one row in `settings`, which may be new. Nothing else may have changed.
    differences = [line for line in migrate.compare_counts(after, counted) if not line.startswith("public.settings:")]
    if differences:
        raise NativeError(f"The counts changed during the move: {differences[0]}. The source is untouched.")
    if listing(cfg.data_dir) != before:
        raise NativeError(f"Something changed {cfg.data_dir} during the move. Was it started? The copy cannot be trusted.")
    rows = sum(after.values())
    report = {
        "at": time.time(),
        "source": str(source_root),
        "entries": len(before),
        "bytes": sum(e[1] for e in before.values()),
        "seconds": round(time.monotonic() - began),
        "tables": after,
        "rows": rows,
        "collation_rebuilt": rebuilt,
        "ok": True,
    }
    write_private(layout.data / "migration" / "move-in.json", json.dumps(report, indent=1))
    write_private(layout.moved_from_file, json.dumps({"source": str(source_root), "at": report["at"]}))
    say(f"Verified: {len(before):,} entries match the source, and {len(after)} tables hold {rows:,} rows.")
    say(f"The source at {cfg.data_dir} was not changed. Delete it yourself, once you are sure.")
    say("Nothing applies yet: a hold is on. Open AXIOM, look around, then release it (Settings, or `axiom hold off`).")
    return 0


def _destination(layout, ports):
    """The installation being moved into, configured the way `boot` configures it."""
    from .desktop import environment

    env = {**os.environ, **environment(layout, ports)}
    return load(layout.support, environ=env)


def main(layout, argv):
    parser = argparse.ArgumentParser(prog="axiom move-in", description=__doc__.splitlines()[0])
    parser.add_argument("--from", dest="source", required=True, help="The checkout that holds .env and .data")
    parser.add_argument("--plan", action="store_true", help="Say what would happen; change nothing")
    parser.add_argument("--replace", action="store_true", help="Rename data already here aside (never deleted)")
    parser.add_argument("--sample", type=int, default=200, help="Files to hash beyond the database and keys")
    args = parser.parse_args(argv)
    if args.plan:
        return plan(layout, load(Path(args.source).expanduser().resolve(), environ={}))
    return move(layout, args.source, replace=args.replace, sample=args.sample)
