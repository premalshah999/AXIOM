"""Move a Docker installation to the native data directory: the database, then the files.

Three rules come from how this Mac has been hurt before:

* **Docker is read-only from here.** Every command against Docker is built by `Source`: `pg_dump`, `psql` selects, and
  `find`/`tar`/`sha256sum` in a throwaway container that mounts the volume read-only. Nothing here can delete or change
  anything in Docker; rolling back is starting the containers again.
* **Never fill the disk.** A copy loop once did (2026-10-21). The free space is measured before every batch and the move
  stops at a floor, keeping what it has. There is one copy per file and no intermediate dump: pg_dump streams into
  pg_restore.
* **Every step can be run again.** The files are a sync, not a journal: each run compares the source's listing with
  what is here and fetches only the difference, newest application first. It can start while Docker still runs, at low
  priority, to shorten the downtime, and the last pass picks up what changed since.

The 15.9 GB of run files is 10 GB of images and PDFs that do not compress and 5.8 GB of HTML and JSON that does (6:1 on a
sample of 24 production runs). `ditto --hfsCompression` stores those files compressed in the file system, so nothing
that reads them has to know.
"""

import json
import os
import random
import shutil
import subprocess
import time
from collections import defaultdict
from dataclasses import dataclass, field

from . import pg
from .env import NativeError

GIB = 1024**3
DEFAULT_KEEP_FREE_GB = 4.0
BATCH_BYTES = 64 * 1024 * 1024  # The disk is measured between batches, so this is how closely the floor is kept.
BATCH_FILES = 4000
RESTORE_MIN_FREE = 1 * GIB
# The keys that decrypt the mailbox and employer-account passwords. A native data dir that made its own before the move
# holds a different key, and every saved password would be unreadable: the move must say so.
SECRETS = ("mail-key", "connections.enc", "access-token")
SKIP_NAMES = {"__pycache__"}
TABLES = (
    "select schemaname||'.'||tablename from pg_tables "
    "where schemaname not in ('pg_catalog','information_schema') order by 1"
)
CLIENTS = (
    "select count(*) from pg_stat_activity where datname=current_database() and pid<>pg_backend_pid() "
    "and backend_type='client backend'"
)


class Source:
    """Docker, read-only. Tests pin that nothing else in this module talks to Docker."""

    def __init__(self, db_container, volume, image=None, runner=subprocess.run):
        self.db_container, self.volume, self.image, self.runner = db_container, volume, image, runner

    def psql_argv(self, sql):
        return ["docker", "exec", self.db_container, "psql", "-U", pg.USER, "-d", pg.DATABASE, "-XAtc", sql]

    def dump_argv(self):
        # Custom format for pg_restore; light compression, because it crosses a pipe on this computer only.
        return ["docker", "exec", self.db_container, "pg_dump", "-U", pg.USER, "-d", pg.DATABASE, "-Fc", "-Z", "1"]

    def helper_argv(self, *command):
        """A throwaway container with the volume mounted read-only, not `docker exec` into the app: the app is stopped
        for the cut-over. Uses the database's image, which is already here and has find, tar and sha256sum."""
        return [
            "docker", "run", "--rm", "-i", "--pull", "never", "--network", "none", "--memory", "256m",
            "-v", f"{self.volume}:/data:ro", "--entrypoint", "nice", self.image or self.db_image(), "-n", "19", *command,
        ]  # fmt: skip

    def db_image(self):
        self.image = self.check(
            ["docker", "inspect", "--format", "{{.Config.Image}}", self.db_container], "the database container"
        ).strip()
        return self.image

    def check(self, argv, what, **kwargs):
        try:
            done = self.runner(argv, capture_output=True, **{"text": True, **kwargs})
        except FileNotFoundError:
            raise NativeError("Docker is not installed here, so there is no Docker installation to migrate.") from None
        if done.returncode:
            detail = done.stderr if isinstance(done.stderr, str) else done.stderr.decode("utf-8", "replace")
            raise NativeError(f"Could not read {what} from Docker: {detail.strip()[-300:]}")
        return done.stdout

    def query(self, sql):
        return self.check(self.psql_argv(sql), "the database").strip()

    def listing(self):
        """Every file in the volume as {path: (size, mtime)}. NUL-separated: a file name may hold anything."""
        out = self.check(
            self.helper_argv("find", "/data", "-type", "f", "-printf", "%P\\0%s\\0%T@\\0"), "the data volume", text=False
        )
        parts = out.split(b"\0")
        files = {}
        for i in range(0, len(parts) - 2, 3):
            path = parts[i].decode("utf-8", "surrogateescape")
            if not SKIP_NAMES.intersection(path.split("/")):
                files[path] = (int(parts[i + 1]), float(parts[i + 2]))
        return files

    def tar_argv(self):
        return self.helper_argv("tar", "-C", "/data", "-cf", "-", "--null", "-T", "-")

    def hashes(self, paths):
        listing = b"\0".join(("/data/" + p).encode("utf-8", "surrogateescape") for p in paths) + b"\0"
        out = self.check(self.helper_argv("xargs", "-0", "sha256sum"), "file hashes", input=listing, text=False)
        found = {}
        for line in out.decode("utf-8", "surrogateescape").splitlines():
            digest, _, path = line.partition("  ")
            found[path.removeprefix("/data/")] = digest
        return found


def source_from(cfg, args):
    project = cfg.get("COMPOSE_PROJECT_NAME") or "axiom"
    return Source(
        getattr(args, "db_container", None) or f"{project}-db-1",
        getattr(args, "volume", None) or f"{project}_applicant-data",
    )


def free_bytes(path):
    probe = path
    while not probe.exists():
        probe = probe.parent
    return shutil.disk_usage(probe).free


def human(n):
    return f"{n / GIB:.2f} GB" if abs(n) >= GIB / 10 else f"{n / 1024**2:.1f} MB"


# ---- files -----------------------------------------------------------------------------------------------------------


@dataclass
class FilePlan:
    """What to fetch, in order. Keys and owner documents first, then whole runs, newest first."""

    batches: list = field(default_factory=list)  # each: list of (path, size)
    total_bytes: int = 0
    total_files: int = 0
    kept_native: int = 0
    runs_wanted: int = 0
    runs_total: int = 0


def group_of(path):
    parts = path.split("/")
    return parts[1] if parts[0] == "runs" and len(parts) > 2 else ""


def needs_copy(cfg, path, size, mtime, plan):
    """Missing here, or changed in Docker since it was fetched. A file the native app has written since is never
    overwritten: its modification time is the newer one."""
    try:
        here = (cfg.data_dir / path).stat()
    except (FileNotFoundError, NotADirectoryError):
        return True
    if here.st_size == size:
        return False
    if mtime > here.st_mtime:
        return True
    plan.kept_native += 1
    return False


def plan_files(cfg, listing):
    plan = FilePlan()
    essentials, runs, newest = [], defaultdict(list), defaultdict(float)
    run_ids = set()
    for path, (size, mtime) in listing.items():
        run = group_of(path)
        if run:
            run_ids.add(run)
        if not needs_copy(cfg, path, size, mtime, plan):
            continue
        if run:
            runs[run].append((path, size))
            newest[run] = max(newest[run], mtime)
        else:
            essentials.append((path, size))
    plan.runs_total, plan.runs_wanted = len(run_ids), len(runs)
    order = [essentials, *(runs[r] for r in sorted(runs, key=lambda r: newest[r], reverse=True))]
    batch, weight = [], 0
    for position, group in enumerate(order):
        size = sum(s for _, s in group)
        # The keys travel alone: whatever the floor does to the runs, they arrive.
        if batch and (position == 1 or weight + size > BATCH_BYTES or len(batch) + len(group) > BATCH_FILES):
            plan.batches.append(batch)
            batch, weight = [], 0
        batch.extend(group)
        weight += size
        plan.total_bytes += size
        plan.total_files += len(group)
    if batch:
        plan.batches.append(batch)
    return plan


def fetch_batch(cfg, source, batch, incoming):
    """Stream one batch out of the volume and into place, compressed where the file system can. One short-lived second
    copy per batch is the whole overhead."""
    shutil.rmtree(incoming, ignore_errors=True)
    incoming.mkdir(parents=True, mode=0o700)
    listing = b"\0".join(path.encode("utf-8", "surrogateescape") for path, _ in batch) + b"\0"
    reader = subprocess.Popen(source.tar_argv(), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    writer = subprocess.Popen(
        ["tar", "-xpf", "-", "-C", str(incoming)], stdin=reader.stdout, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    reader.stdout.close()
    reader.stdin.write(listing)
    reader.stdin.close()
    _, extract_error = writer.communicate()
    source_error = reader.stderr.read()
    reader.stderr.close()
    if reader.wait() or writer.returncode:
        detail = (source_error + extract_error).decode("utf-8", "replace").strip()[-300:]
        raise NativeError(f"Copying from the data volume failed: {detail}")
    if shutil.which("ditto"):
        done = subprocess.run(
            ["ditto", "--hfsCompression", str(incoming), str(cfg.data_dir)], capture_output=True, text=True
        )
        if done.returncode:
            raise NativeError("ditto failed: " + done.stderr.strip()[-300:])
    else:
        for folder, _, names in os.walk(incoming):
            target = cfg.data_dir / os.path.relpath(folder, incoming)
            target.mkdir(parents=True, exist_ok=True, mode=0o700)
            for name in names:
                os.replace(os.path.join(folder, name), target / name)
    shutil.rmtree(incoming, ignore_errors=True)
    wrong = [p for p, size in batch if not _has_size(cfg.data_dir / p, size)]
    if wrong:
        raise NativeError(f"{len(wrong)} file(s) arrived with the wrong size, for example {wrong[0]}. Run it again.")


def _has_size(path, size):
    try:
        return path.stat().st_size == size
    except OSError:
        return False


def write_report(cfg, name, report):
    cfg.migration.mkdir(parents=True, exist_ok=True, mode=0o700)
    target = cfg.migration / name
    target.write_text(json.dumps(report, indent=1))
    target.chmod(0o600)


def files(cfg, source, keep_free_gb=DEFAULT_KEEP_FREE_GB, sample=50, say=print, listing=None):
    """Fetch what is missing. Returns 0 when everything is here, 3 when the disk floor stopped it (run it again later)."""
    floor = int(keep_free_gb * GIB)
    os.umask(0o077)
    cfg.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    say(f"Reading the file list from the volume {source.volume} ...")
    listing = source.listing() if listing is None else listing
    refuse_other_keys(cfg, source, listing)
    plan = plan_files(cfg, listing)
    say(
        f"{len(listing):,} files in Docker, {plan.runs_total:,} runs. To fetch: {plan.total_files:,} files, "
        f"{human(plan.total_bytes)}, in {plan.runs_wanted:,} runs. Free: {human(free_bytes(cfg.data_dir))}, "
        f"stopping at {human(floor)}."
    )
    incoming = cfg.migration / "incoming"
    moved_bytes, moved_files, stopped, began = 0, 0, False, time.monotonic()
    for index, batch in enumerate(plan.batches, 1):
        size = sum(s for _, s in batch)
        free = free_bytes(cfg.data_dir)
        if free - size < floor:
            stopped = True
            say(f"Stopping: {human(free)} free and the next batch is {human(size)}; the floor is {human(floor)}.")
            break
        fetch_batch(cfg, source, batch, incoming)
        moved_bytes += size
        moved_files += len(batch)
        if index % 10 == 0 or index == len(plan.batches):
            say(
                f"  {moved_files:,} of {plan.total_files:,} files, {human(moved_bytes)} in "
                f"{time.monotonic() - began:.0f} s; {human(free_bytes(cfg.data_dir))} free."
            )
    # The difference again, from the same listing: what this leaves in Docker, for the report and the next run.
    left = plan_files(cfg, listing)
    report = {
        "at": time.time(),
        "volume": source.volume,
        "source_files": len(listing),
        "source_bytes": sum(s for s, _ in listing.values()),
        "moved_files": moved_files,
        "moved_bytes": moved_bytes,
        "left_files": left.total_files,
        "left_bytes": left.total_bytes,
        "left_runs": left.runs_wanted,
        "kept_native": left.kept_native,
        "complete": left.total_files == 0,
        "stopped_at_floor": stopped,
        "free_bytes": free_bytes(cfg.data_dir),
    }
    report["sampled"] = verify_sample(cfg, source, listing, sample) if sample else 0
    write_report(cfg, "files-report.json", report)
    refuse_other_keys(cfg, source, listing)
    if left.total_files:
        say(
            f"Not finished: {left.runs_wanted:,} runs ({human(left.total_bytes)}) are still only in Docker, the oldest "
            "ones. Nothing is lost: free some disk and run this again, and keep the Docker volume until it says complete."
        )
        return 3
    say(f"Complete: every file in Docker is here. Sampled {report['sampled']} files by hash; all match.")
    return 0


def refuse_other_keys(cfg, source, listing):
    """Compared by hash, before anything moves and again after: a key of its own has the same length as Docker's, so the
    size check that serves every other file would pass it."""
    present = [name for name in SECRETS if name in listing and (cfg.data_dir / name).exists()]
    theirs = source.hashes(present) if present else {}
    differ = [name for name in present if theirs.get(name) != _sha256(cfg.data_dir / name)]
    if differ:
        raise NativeError(
            f"{', '.join(differ)} here differ from Docker's: this data directory started on its own before the move and "
            "has its own keys, which cannot read Docker's saved passwords. Stop it, move those files aside and run this "
            "again."
        )


def verify_sample(cfg, source, listing, count):
    """Sizes are checked for every file. These are hashed on both sides, as a check that tar and ditto kept the bytes."""
    here = [p for p, (size, _) in listing.items() if _has_size(cfg.data_dir / p, size)]
    chosen = random.sample(here, min(count, len(here)))
    if not chosen:
        return 0
    theirs = source.hashes(chosen)
    wrong = [p for p in chosen if theirs.get(p) != _sha256(cfg.data_dir / p) and (cfg.data_dir / p).stat().st_mtime <= listing[p][1]]
    if wrong:
        raise NativeError(f"{len(wrong)} of {len(chosen)} sampled files differ from Docker's, for example {wrong[0]}.")
    return len(chosen)


def _sha256(path):
    import hashlib

    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


# ---- database --------------------------------------------------------------------------------------------------------


def table_counts(query, tables):
    """Exact counts, one statement, so that every table is counted against the same snapshot."""
    if not tables:
        return {}
    parts = [f"select '{t}', count(*) from {_quoted(t)}" for t in tables]
    out = query(" union all ".join(parts))
    return {line.split("|")[0]: int(line.split("|")[1]) for line in out.splitlines()}


def _quoted(table):
    schema, name = table.split(".", 1)
    return f'"{schema}"."{name}"'


def compare_counts(ours, theirs):
    """Every difference, as lines. Empty means the same tables with the same row counts."""
    return [
        f"{table}: Docker {theirs.get(table, 'missing')}, here {ours.get(table, 'missing')}"
        for table in sorted(set(ours) | set(theirs))
        if ours.get(table) != theirs.get(table)
    ]


def restore(cfg, bin_dir, source, say=print):
    """pg_dump in the container into pg_restore here, with no file in between. The disk is watched while it runs."""
    env = {
        **os.environ,
        "PGPASSWORD": cfg.password,
        # Index builds in memory rather than on a scarce disk; a crash here only means restoring again.
        "PGOPTIONS": "-c maintenance_work_mem=128MB -c synchronous_commit=off",
    }
    errors = cfg.migration / "restore-errors.log"
    with open(errors, "wb") as log:
        dump = subprocess.Popen(source.dump_argv(), stdout=subprocess.PIPE, stderr=log)
        loader = subprocess.Popen(
            [str(bin_dir / "pg_restore"), *pg.connection(cfg), "--no-owner", "--no-privileges", "--exit-on-error"],
            stdin=dump.stdout,
            stdout=subprocess.DEVNULL,
            stderr=log,
            env=env,
        )
        dump.stdout.close()
        while loader.poll() is None:
            if free_bytes(cfg.data_dir) < RESTORE_MIN_FREE:
                loader.terminate()
                dump.terminate()
                loader.wait()
                dump.wait()
                raise NativeError(f"Stopped the restore: under {human(RESTORE_MIN_FREE)} free. Free some disk and run it again.")
            time.sleep(2)
        dump_code = dump.wait()
    if dump_code or loader.returncode:
        raise NativeError(f"The restore failed (pg_dump {dump_code}, pg_restore {loader.returncode}). See {errors}.")


def rewrite_queued_paths(bin_dir, cfg):
    """Queued runs carry the absolute path of their resume, which in Docker is under /app/.data. Every other state goes
    back through Service.queue, which writes a new packet from DATA_DIR, so only these need it."""
    target = str(cfg.data_dir).replace("'", "''")
    pg.psql(
        bin_dir,
        cfg,
        "update runs set packet = replace(packet::text, '/app/.data/', '" + target + "/')::json "
        "where state = 'queued' and packet::text like '%/app/.data/%'",
        timeout=300,
    )
    left = pg.psql(bin_dir, cfg, "select count(*) from runs where state='queued' and packet::text like '%/app/.data/%'")
    if left != "0":
        raise NativeError(f"{left} queued run(s) still point at /app/.data.")


def set_hold(bin_dir, cfg, stage, reason):
    """execution_v2_deployment_hold: while `draining`, nothing new starts (admission, retries and the autopilot read it)."""
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())
    value = json.dumps({"at": stamp, "stage": stage, "reason": reason, "updated_at": stamp}).replace("'", "''")
    pg.psql(
        bin_dir,
        cfg,
        f"insert into settings(key, value) values ('execution_v2_deployment_hold', '{value}'::json) "
        "on conflict (key) do update set value = excluded.value",
    )


def database(cfg, source, bin_dir, *, replace=False, keep_free_gb=DEFAULT_KEEP_FREE_GB, say=print):
    """Restore Docker's database here and prove it arrived. Needs the native server up and the Docker app stopped."""
    report_file = cfg.migration / "db-report.json"
    marker = cfg.migration / "db-restore.started"
    cfg.migration.mkdir(parents=True, exist_ok=True, mode=0o700)
    tables_here = pg.psql(bin_dir, cfg, "select count(*) from pg_tables where schemaname in ('public','dbos')")
    interrupted = marker.exists()
    if tables_here != "0" and not interrupted and not replace:
        if report_file.exists() and json.loads(report_file.read_text()).get("ok"):
            say(f"The database was already restored and verified ({report_file}). Nothing to do.")
            return 0
        raise NativeError(
            "The native database already has tables, and this tool did not put them there. If it may be replaced, add "
            "--replace."
        )
    clients = source.query(CLIENTS)
    if clients != "0":
        raise NativeError(
            f"{clients} client(s) are connected to Docker's database: the app is still running, and anything it writes "
            "after the dump would be lost. Stop it first: docker compose stop app"
        )
    size = int(source.query("select pg_database_size(current_database())"))
    needed = int(size * 1.15) + RESTORE_MIN_FREE + int(keep_free_gb * GIB)
    if free_bytes(cfg.data_dir) < needed:
        raise NativeError(
            f"The database needs about {human(needed)} free (it is {human(size)}, plus WAL and the {keep_free_gb:g} GB "
            f"floor), and {human(free_bytes(cfg.data_dir))} is free."
        )
    marker.write_text(str(time.time()))  # `axiom start` refuses a half-restored database while this exists.
    report_file.unlink(missing_ok=True)
    if tables_here != "0":
        say("Clearing the unfinished restore ...")
        pg.psql(bin_dir, cfg, f"drop database if exists {pg.DATABASE} with (force)", database="postgres", timeout=120)
    pg.ensure_database(bin_dir, cfg)
    began = time.monotonic()
    say(f"Restoring {human(size)} from {source.db_container}. This reads Docker and changes nothing there.")
    restore(cfg, bin_dir, source, say)
    say(f"Restored in {time.monotonic() - began:.0f} s. Counting every table on both sides ...")
    theirs = table_counts(source.query, source.query(TABLES).splitlines())
    ask = lambda sql: pg.psql(bin_dir, cfg, sql, timeout=900)  # noqa: E731
    ours = table_counts(ask, ask(TABLES).splitlines())
    differences = compare_counts(ours, theirs)
    report = {
        "at": time.time(),
        "source": source.db_container,
        "source_bytes": size,
        "seconds": round(time.monotonic() - began),
        "tables": {t: {"docker": theirs.get(t), "native": ours.get(t)} for t in sorted(set(ours) | set(theirs))},
        "differences": differences,
        "ok": not differences,
    }
    if differences:
        write_report(cfg, "db-report.json", report)
        raise NativeError(f"The counts differ in {len(differences)} table(s), first: {differences[0]}. See {report_file}.")
    pg.psql(bin_dir, cfg, "analyze", timeout=900)
    rewrite_queued_paths(bin_dir, cfg)
    # Nothing starts on the first native start until the owner has seen the dashboard (`axiom hold off`).
    set_hold(bin_dir, cfg, "draining", "native cut-over: release with `axiom hold off` once the dashboard looks right")
    write_report(cfg, "db-report.json", report)
    marker.unlink()
    rows = sum(v["native"] or 0 for v in report["tables"].values())
    say(f"Verified: {len(ours)} tables and {rows:,} rows match Docker exactly. Report: {report_file}")
    return 0


# ---- plan ------------------------------------------------------------------------------------------------------------


# Share of each kind that is left after file-system compression, from the production sample in the module docstring.
COMPRESSED = {".html": 0.16, ".json": 0.16, ".jsonl": 0.16, ".txt": 0.16, ".md": 0.16}


def estimate_stored(listing_part):
    total = 0
    for path, size in listing_part:
        total += int(size * COMPRESSED.get(os.path.splitext(path)[1], 1.0))
    return total


def plan(cfg, source, keep_free_gb=DEFAULT_KEEP_FREE_GB, say=print):
    """What the move would do. Reads Docker, writes nothing anywhere."""
    size = int(source.query("select pg_database_size(current_database())"))
    clients = source.query(CLIENTS)
    say(f"Docker database: {source.db_container}, {human(size)}, {clients} client(s) connected.")
    say(f"Reading the file list from the volume {source.volume} ...")
    listing = source.listing()
    todo = plan_files(cfg, listing)
    files_stored = estimate_stored(p for batch in todo.batches for p in batch)
    free = free_bytes(cfg.data_dir)
    floor = int(keep_free_gb * GIB)
    db_here = 0 if (cfg.migration / "db-report.json").exists() else int(size * 1.15)
    room = free - db_here - floor
    say(f"Volume: {len(listing):,} files, {human(sum(s for s, _ in listing.values()))}, {todo.runs_total:,} runs.")
    say(f"To fetch: {todo.total_files:,} files, {human(todo.total_bytes)}, about {human(files_stored)} on disk once compressed.")
    say(f"Free here: {human(free)}. Database: {human(db_here)}. Floor kept free: {human(floor)}. Room for files: {human(room)}.")
    if room >= files_stored:
        say("Everything fits.")
        return 0
    fitted, count = 0, 0
    for batch in todo.batches:
        stored = estimate_stored(batch)
        if fitted + stored > room:
            break
        fitted += stored
        count += 1
    say(
        f"Not everything fits: about {human(max(0, files_stored - room))} short. The newest {count} of "
        f"{len(todo.batches)} batches would move, and the oldest runs stay in the Docker volume until there is room."
    )
    say("`docker system df` shows what Docker could free: unused images and build cache are not your data.")
    return 3
