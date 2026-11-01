#!/usr/bin/env python3
"""Install one build's data, upgrade it to another, and prove nothing was lost: on a throwaway home, with real bundles.

    python3 scripts/dev/upgrade_check.py --old /Applications/AXIOM.app --new path/to/AXIOM.app [--work dir] [--keep]

Nothing here can reach an installation in use. HOME is a new empty folder, so the bundle's own layout puts its data, ports, database and
Chromes there; its `.env` says ENABLE_WORKERS=false and holds no keys, so nothing applies and nothing leaves this computer; the bundles
are only run, never written.

  1 install   the old build starts on an empty home, and synthetic data goes in: rows, an application in each state, saved files
  2 upgrade   the new build starts on it: every row and file must be as it was, the schema as the new code expects
  3 downgrade the old build starts again on what the new one left (a rollback): it must come up and still read everything
  4 failure   a migration that fails half way: one step applies and the next is refused. The new build must not come up pretending,
              nothing may be lost, and once the cause is gone the next start must finish the job

Exit status 0 only when all four hold. Standard library only.
"""

import argparse
import hashlib
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

START_SECONDS = 150
FAIL_WAIT_SECONDS = 45
# A row of each table the app cannot lose, and a run in each state a stop or an upgrade could disturb.
SEED = """
insert into jobs (id, identity, url, title, company, location, description, ats, status, demo, created_at, score, reason)
  select 'job-' || n, 'example.test/jobs/' || n, 'https://boards.example.test/acme/jobs/' || n, 'Engineer ' || n, 'Acme Example', 'Remote', '',
         'greenhouse', s, false, '2026-10-01T00:00:00+00:00', 0, ''
  from (values (1, 'confirmed'), (2, 'submission_unknown'), (3, 'waiting_answer'), (4, 'ready'), (5, 'paused')) as v(n, s)
  on conflict do nothing;
insert into runs (id, job_id, state, mode, packet, elapsed, cost, model_calls, reason, receipt, created_at)
  select 'run-' || n, 'job-' || n, s, 'submit', '{"profile": {"name": "Alex Example"}}'::json, 1.5, 0.01, 1, 'seeded', '{}'::json, '2026-10-01T00:00:00+00:00'
  from (values (1, 'confirmed'), (2, 'submission_unknown'), (3, 'waiting_answer'), (5, 'paused')) as v(n, s)
  on conflict do nothing;
insert into settings (key, value) values
  ('profile', '{"name": "Alex Example", "email": "alex@example.test", "facts": {"first_name": "Alex"}}'::json),
  ('owner_answer:seed', '{"question": "Do you enjoy testing?", "answer": "Yes"}'::json)
  on conflict (key) do nothing;
"""
TABLES = ("jobs", "runs", "settings", "resumes", "events", "sources", "budgets", "reviews", "mailboxes", "application_outbox")
SEEDED = """select md5(string_agg(row, ',' order by row)) from (
  select id || state || mode || coalesce(reason, '') || packet::text as row from runs where id like 'run-%'
  union all select id || status || url || title from jobs where id like 'job-%'
  union all select key || value::text from settings where key in ('profile', 'owner_answer:seed')) as seeded"""


class Stop(Exception):
    pass


class Home:
    """A bundle's view of the throwaway home: its command, its database, its files."""

    def __init__(self, root):
        self.root = Path(root)
        self.support = self.root / "Library" / "Application Support" / "AXIOM"
        self.data = self.support / "data"
        self.logs = self.root / "Library" / "Logs" / "AXIOM"
        self.env = {"HOME": str(self.root), "PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "TMPDIR": str(self.root / "tmp"), "LANG": "en_US.UTF-8"}
        self.axiom = None

    def use(self, bundle):
        self.axiom = Path(bundle) / "Contents" / "Resources" / "bin" / "axiom"

    def run(self, *args, timeout=120):
        return subprocess.run([self.axiom, *args], env=self.env, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)

    def sql(self, statement):
        done = self.run("psql", "-XAt", "-c", statement, timeout=60)
        if done.returncode:
            raise Stop(f"psql: {(done.stderr or done.stdout)[-300:]}")
        return done.stdout.strip()

    def state(self):
        done = self.run("status", "--json", timeout=30)
        try:
            return json.loads(done.stdout)
        except ValueError:
            return {}

    def start(self, wait=START_SECONDS):
        """As the window does. True when every service came up in time."""
        subprocess.Popen([self.axiom, "boot"], env=self.env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline:
            state = self.state()
            children = state.get("children") or {}
            if state.get("alive") and state.get("phase") == "running" and children:
                return True
            time.sleep(2)
        return False

    def stop(self):
        self.run("stop", timeout=300)
        deadline = time.monotonic() + 30
        while self.state().get("alive") and time.monotonic() < deadline:
            time.sleep(1)

    def healthy(self):
        ports = json.loads((self.support / "ports.json").read_text())
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(f"http://127.0.0.1:{ports['app']}/healthz", timeout=10) as answer:
                return answer.status == 200 and json.load(answer).get("status") == "ok"
        except OSError:
            return False

    def fingerprint(self):
        counts = {table: self.sql(f"select count(*) from {table}") for table in TABLES}
        return {"counts": counts, "seeded": self.sql(SEEDED), "files": self.files()}

    def files(self):
        found = {}
        for folder in ("runs/run-1", "resumes"):
            for path in sorted((self.data / folder).rglob("*")):
                if path.is_file():
                    found[str(path.relative_to(self.data))] = hashlib.sha256(path.read_bytes()).hexdigest()
        found[".env"] = hashlib.sha256((self.support / ".env").read_bytes()).hexdigest()
        return found


class Check:
    def __init__(self):
        self.failures = []

    def __call__(self, ok, text):
        print(f"  {'ok  ' if ok else 'FAIL'}  {text}", flush=True)
        if not ok:
            self.failures.append(text)


def same(check, before, now, stage):
    for table, count in before["counts"].items():
        # The app itself may add to these at start (its own settings, a run's events). Nothing may go away.
        if table in {"settings", "events", "sources"}:
            check(int(now["counts"][table]) >= int(count), f"{stage}: {table} kept its {count} rows")
        else:
            check(now["counts"][table] == count, f"{stage}: {table} still has {count} rows")
    check(now["seeded"] == before["seeded"], f"{stage}: every seeded job, run and setting is unchanged")
    check(now["files"] == before["files"], f"{stage}: {len(before['files'])} saved files and .env are unchanged")


def seed(home):
    home.sql(SEED)
    run_dir = home.data / "runs" / "run-1"
    (run_dir / "steps").mkdir(parents=True, exist_ok=True)
    (run_dir / "answers.json").write_text('{"q": {"label": "First name", "answer": "Alex"}}')
    (run_dir / "steps" / "01-form.html").write_text("<h1>A saved page</h1>")
    (home.data / "resumes").mkdir(exist_ok=True)
    (home.data / "resumes" / "seed.pdf").write_bytes(b"%PDF-1.4 synthetic")


def check_schema(home, check, stage):
    """The new build's own comparison of its models with the database."""
    done = home.run("smoke", "--no-fill", "--hold-expected", timeout=120)
    line = next((l for l in done.stdout.splitlines() if "migrations" in l), "")
    check(line.strip().startswith("ok"), f"{stage}: schema as the code expects ({line.strip() or 'no answer'})")


def half_way(home, check, new, before):
    """One step of a migration applies and the next is refused, which is what a full disk or a bug looks like from here."""
    home.use(new)
    home.sql("drop table if exists application_outbox")
    home.sql("alter table reviews drop column if exists context")
    home.sql("""create or replace function refuse_alter() returns event_trigger language plpgsql as $$
      begin raise exception 'a migration step refused, on purpose'; end $$""")
    home.sql("create event trigger refuse_alter on ddl_command_start when tag in ('ALTER TABLE') execute function refuse_alter()")
    home.stop()
    came_up = home.start(wait=FAIL_WAIT_SECONDS)
    check(not (came_up and home.healthy()), "failure: the build did not come up pretending, with a migration not done")
    log = (home.logs / "app.log").read_text(errors="replace") if (home.logs / "app.log").exists() else ""
    check("refused, on purpose" in log, "failure: the log says why")
    home.stop()
    # The database itself is up again for the repair, as a person would find it: the next start finishes the job.
    started = home.start(wait=10)  # Brings PostgreSQL up; the app keeps failing behind it.
    home.sql("drop event trigger refuse_alter")
    home.sql("drop function refuse_alter")
    home.stop()
    check(home.start(), "failure: with the cause gone, the next start comes up" + ("" if started else " (first try was still failing, as it should)"))
    check(home.healthy(), "failure: the API answers")
    check(home.sql("select count(*) from information_schema.columns where table_name='reviews' and column_name='context'") == "1",
          "failure: the column that was refused is there now")
    check(home.sql("select count(*) from information_schema.tables where table_name='application_outbox'") == "1", "failure: the table is back")
    now = home.fingerprint()
    now["counts"]["application_outbox"], now["counts"]["reviews"] = before["counts"]["application_outbox"], before["counts"]["reviews"]
    same(check, before, now, "failure")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--old", required=True)
    parser.add_argument("--new", required=True)
    parser.add_argument("--work", help="Where the throwaway home goes (default: a new temporary folder)")
    parser.add_argument("--keep", action="store_true", help="Leave the home for a look")
    options = parser.parse_args(argv)
    work = Path(options.work).expanduser() if options.work else Path(tempfile.gettempdir())
    work.mkdir(parents=True, exist_ok=True)
    # Short: PostgreSQL's socket path has a length limit, and the database refuses to start beyond it.
    root = Path(os.path.realpath(tempfile.mkdtemp(prefix="axu-", dir=work)))
    if str(root).startswith(os.path.expanduser("~/Library")):
        print("The throwaway home may not be inside the real Library", file=sys.stderr)
        return 2
    home = Home(root)
    (root / "tmp").mkdir()
    home.support.mkdir(parents=True, mode=0o700)
    (home.support / ".env").write_text(
        f"POSTGRES_PASSWORD={secrets.token_hex(16)}\nENABLE_WORKERS=false\nENABLE_DEMO=false\nTELEGRAM_ENABLED=false\nTIMEZONE=UTC\n"
    )
    (home.support / ".env").chmod(0o600)
    check = Check()
    began = time.monotonic()
    try:
        print(f"Throwaway home: {root}")
        print("1 install: the old build, with synthetic data")
        home.use(options.old)
        if not home.start():
            raise Stop("The old build did not start on an empty home")
        seed(home)
        before = home.fingerprint()
        ports = (home.support / "ports.json").read_text()
        check(before["counts"]["runs"] == "4", "install: four runs seeded")
        home.stop()

        print("2 upgrade: the new build on that data")
        home.use(options.new)
        check(home.start(), "upgrade: the new build came up")
        check(home.healthy(), "upgrade: the API answers")
        check((home.support / "ports.json").read_text() == ports, "upgrade: the ports stayed the same")
        same(check, before, home.fingerprint(), "upgrade")
        check_schema(home, check, "upgrade")
        home.stop()

        print("3 downgrade: the old build on what the new one left")
        home.use(options.old)
        check(home.start(), "downgrade: the old build came up on the newer schema")
        check(home.healthy(), "downgrade: the API answers")
        same(check, before, home.fingerprint(), "downgrade")
        home.sql("insert into settings (key, value) values ('downgrade_write', '{}'::json) on conflict do nothing")
        check(home.sql("select count(*) from settings where key = 'downgrade_write'") == "1", "downgrade: it can still write")
        before = home.fingerprint()
        print("4 failure: a migration that fails half way")
        half_way(home, check, options.new, before)
        home.stop()
    except (Stop, OSError, subprocess.TimeoutExpired, ValueError) as exc:
        check(False, f"stopped: {type(exc).__name__}: {str(exc)[:400]}")
    finally:
        try:
            home.stop()
        except Exception:
            pass
        if not options.keep and not check.failures:
            shutil.rmtree(root, ignore_errors=True)
    print(f"{'PASSED' if not check.failures else 'FAILED: ' + str(len(check.failures))} in {time.monotonic() - began:.0f} s")
    if check.failures:
        print(f"Left for a look: {root}")
    return 1 if check.failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
