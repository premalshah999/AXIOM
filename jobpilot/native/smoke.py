"""`axiom smoke`: prove an installation works, not only that it started.

Two halves, because they answer different questions:

* `check_installation` looks at the running app from outside, and only reads: the services, the API, the database, that the
  schema has everything this code expects, that the workers tick, that every file the dashboard asks for is served.
* `dry_fill` proves the shipped code can apply. A throwaway copy of the engine, from these same files, in an empty folder with
  its own SQLite database and its own headless Chrome, opens a job page on a site this process hosts on 127.0.0.1, presses
  Apply, fills the form, uploads a resume, and stops short of the commit (a dry run); then does it again and submits, to that
  same local site, which must have received exactly one application. No real employer is contacted, no model is called, and
  nothing of the running app's is touched: not its database, its files or its Chromes.

`jobpilot verify` covers a new installation through the API with the demo form, but a demo job skips the navigator (the engine's
`job["demo"]` branches), and it refuses an installation that has a profile. This one takes the path a real job takes.
"""

import argparse
import asyncio
import http.server
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

HEARTBEAT_SECONDS = 180  # workers.monitor_scheduler calls a heartbeat older than this degraded
DRY_FILL_SECONDS = 240

JOB_PAGE = """<!doctype html><html lang="en"><meta charset="utf-8"><title>Software Engineer at ExampleSoft</title>
<body><header><a href="/">ExampleSoft Careers</a></header><main><h1>Software Engineer</h1><p>ExampleSoft · Remote</p>
<p>Build and run the systems behind ExampleSoft's product with Python and PostgreSQL. A synthetic posting on this computer.</p>
<a href="/apply/SMOKE">Apply for this job</a></main></body></html>"""

FORM_PAGE = """<!doctype html><html lang="en"><meta charset="utf-8"><title>Apply: Software Engineer at ExampleSoft</title>
<body><h1>Apply: Software Engineer</h1><form method="post" action="/submit/SMOKE" enctype="multipart/form-data">
<p><label for="f">First name</label><input id="f" name="first_name" required autocomplete="given-name"></p>
<p><label for="l">Last name</label><input id="l" name="last_name" required autocomplete="family-name"></p>
<p><label for="e">Email</label><input id="e" name="email" type="email" required></p>
<p><label for="r">Resume</label><input id="r" name="resume" type="file" required accept=".pdf"></p>
<button type="submit">Submit application</button></form></body></html>"""

THANKS_PAGE = """<!doctype html><html lang="en"><meta charset="utf-8"><title>Application received</title>
<body><h1>Thank you for applying!</h1><p>Your application has been received.</p></body></html>"""


class Site:
    """The employer in this test: a server on 127.0.0.1 that remembers what it was sent."""

    def __init__(self):
        self.received = []
        site = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def reply(self, body, status=200):
                payload = body.encode()
                self.send_response(status)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def do_GET(self):
                path = urlsplit(self.path).path
                if path.startswith("/jobs/"):
                    return self.reply(JOB_PAGE)
                if path.startswith("/apply/"):
                    return self.reply(FORM_PAGE)
                return self.reply("<h1>Not found</h1>", 404)

            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                site.received.append({"path": self.path, "bytes": len(body), "body": body[:4096].decode("latin-1")})
                return self.reply(THANKS_PAGE)

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def blank_pdf():
    from io import BytesIO

    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    out = BytesIO()
    writer.write(out)
    return out.getvalue()


async def _fill(data_dir, chromium):
    """Runs in the child, whose environment holds nothing of the running app's."""
    import hashlib

    from jobpilot.config import Settings
    from jobpilot.db import Database, Job, Resume, Run
    from jobpilot.discovery import add_job
    from jobpilot.schemas import JobInput
    from jobpilot.service import Service

    config = Settings(
        _env_file=None,
        database_url="",
        dbos_database_url="",
        data_dir=data_dir,
        app_token="axiom-smoke",
        enable_workers=False,
        allow_private_urls=True,
        answer_quality_enabled=False,
        browser_vision_recovery=False,
        failure_reports_enabled=False,
        browser_cdp_url="",
        chromium_path=chromium,
        headless=True,
        repair_agent_enabled=False,  # It would ask a model about a failed run. This copy reaches nothing outside this computer.
        mimo_api_key="no-model-is-called",
        mimo_base_url="http://127.0.0.1:9/v1",  # A closed port: the admission wants a key, and a call could reach nothing.
        llm_provider="mimo",
    ).prepare()
    service = Service(Database(config.database_url), config)

    async def dispatch(_run_id):
        pass

    service.dispatch = dispatch
    pdf = blank_pdf()
    (config.data_dir / "resumes").mkdir(exist_ok=True)
    (config.data_dir / "resumes" / "smoke.pdf").write_bytes(pdf)
    with service.db.exclusive() as s:
        resume = Resume(name="Smoke", filename="smoke.pdf", sha256=hashlib.sha256(pdf).hexdigest(), text="Alex Example, engineer.")
        s.add(resume)
        s.flush()
        resume_id = resume.id
    service.db.set_setting("profile", {"name": "Alex Example", "email": "alex@example.test", "autonomous": True,
                                       "facts": {"first_name": "Alex", "last_name": "Example"}})
    service.db.set_setting("control", {"paused": False, "auto_submit": True})
    site = Site()
    result = {}
    try:
        for mode, number in (("dry_run", 1), ("submit", 2)):
            job, _ = add_job(service.db, JobInput(url=f"http://127.0.0.1:{site.port}/jobs/{number}", title="Software Engineer",
                                                  company="ExampleSoft", location="Remote", description="A synthetic posting."))
            with service.db.exclusive() as s:
                s.get(Job, job["id"]).status = "ready"  # What the ranking leaves a job in; no model reads this one.
            before = len(site.received)
            run = await service.queue(job["id"], mode, resume_id, owner_pick=True)
            await service.execute(run["id"])
            with service.db.session() as s:
                row = s.get(Run, run["id"])
                result[mode] = {"state": row.state, "reason": row.reason[:300], "model_calls": row.model_calls,
                                "received": len(site.received) - before}
        result["fields"] = "Alex" in (site.received[-1]["body"] if site.received else "")
    finally:
        site.close()
    return result


def child_main(data_dir):
    chromium = os.environ.get("CHROMIUM_PATH", "")
    result = asyncio.run(_fill(Path(data_dir), chromium))
    print("SMOKE-RESULT " + json.dumps(result))
    return 0


def judge_fill(result):
    """What the two runs must have done. Empty when they did."""
    problems = []
    dry, submit = result.get("dry_run") or {}, result.get("submit") or {}
    if dry.get("state") != "dry_run_passed":
        problems.append(f"the dry run ended {dry.get('state')}: {dry.get('reason')}")
    if dry.get("received"):
        problems.append("the dry run sent an application")
    if submit.get("state") != "confirmed":
        problems.append(f"the submission ended {submit.get('state')}: {submit.get('reason')}")
    if submit.get("received") != 1:
        problems.append(f"the local site received {submit.get('received')} applications, not one")
    if not result.get("fields"):
        problems.append("the application the local site received did not carry the applicant's name")
    if dry.get("model_calls") or submit.get("model_calls"):
        problems.append("the synthetic form used a model")
    return problems


def child_environment(data_dir, code_path=None):
    """Nothing is inherited: the running app's `.env` values must not reach a copy that applies, even to a local site."""
    env = {
        "HOME": str(data_dir),
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        "LANG": "en_US.UTF-8",
        "TZ": "UTC",
        "DATA_DIR": str(data_dir / "data"),
        "PYTHONUNBUFFERED": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
        "ENABLE_WORKERS": "false",
        "PLAYWRIGHT_BROWSERS_PATH": os.environ.get("PLAYWRIGHT_BROWSERS_PATH", ""),
    }
    if code_path:
        env["PYTHONPATH"] = str(code_path)
    for name in ("OPENSSL_CONF",):
        if os.environ.get(name):
            env[name] = os.environ[name]
    return {k: v for k, v in env.items() if v}


def dry_fill(python=sys.executable, code_path=None, chromium="", timeout=DRY_FILL_SECONDS):
    """Spawn the isolated child and judge it. `python` and `code_path` pick the build under test: a copy not yet installed, or the
    installed app. Returns a list of problems, empty when the build applied to the local site as it should."""
    with tempfile.TemporaryDirectory(prefix="axiom-smoke-") as folder:
        folder = Path(os.path.realpath(folder))
        env = child_environment(folder, code_path)
        if chromium:
            env["CHROMIUM_PATH"] = chromium
        try:
            done = subprocess.run(
                [python, "-m", "jobpilot.native.smoke", "--child", str(folder / "data")],
                env=env, cwd=folder, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired:
            return [f"the dry fill did not finish in {timeout} s"]
        found = [line for line in done.stdout.splitlines() if line.startswith("SMOKE-RESULT ")]
        if done.returncode or not found:
            tail = " ".join((done.stderr or done.stdout).strip().splitlines()[-3:])[:600]
            return [f"the dry fill exited {done.returncode}: {tail}"]
        return judge_fill(json.loads(found[-1].removeprefix("SMOKE-RESULT ")))


# ---- the running installation ----------------------------------------------------------------------------------------


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""


def http_get(url, token=None, timeout=15):
    """(status, body, content type). No proxy: this is always this computer."""
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"} if token else {})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=timeout) as answer:
            return answer.status, answer.read(), answer.headers.get("Content-Type", "")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), exc.headers.get("Content-Type", "")


def expected_schema():
    """{table: {column}} this code's models declare, the feature tables included."""
    from jobpilot.db import Base
    from jobpilot.features._store import FeatureBase
    from jobpilot.features.discovery import tables as _discovery  # noqa: F401 - registers the tables
    from jobpilot.features.knowledge import tables as _knowledge  # noqa: F401
    from jobpilot.features.resumes import tables as _resumes  # noqa: F401

    tables = {}
    for metadata in (Base.metadata, FeatureBase.metadata):
        for table in metadata.tables.values():
            tables.setdefault(table.name, set()).update(c.name for c in table.columns)
    return tables


def schema_gaps(expected, present):
    """What the code expects and the database lacks, as "table" or "table.column". A column the database has and the code does not
    is fine: that is a downgrade, and every migration only adds."""
    gaps = []
    for table, columns in sorted(expected.items()):
        if table not in present:
            gaps.append(table)
        else:
            gaps += [f"{table}.{column}" for column in sorted(columns - present[table])]
    return gaps


def asset_paths(index_html):
    """The files the dashboard's page asks for."""
    return sorted(set(re.findall(r"""(?:src|href)=["'](/assets/[^"']+)["']""", index_html)))


def css_urls(sheet):
    """Fonts and images a stylesheet asks for: a font built to the wrong path was a 404 nobody saw (2026-10-31)."""
    found = set(re.findall(r"""url\(["']?(/assets/[^)"']+)""", sheet))
    return sorted(u.split("#")[0].split("?")[0] for u in found)


def check_assets(base):
    status, body, kind = http_get(base + "/")
    if status != 200 or not kind.startswith("text/html"):
        return Check("dashboard", False, f"/ answered {status} {kind}")
    paths = asset_paths(body.decode(errors="replace"))
    if not paths:
        return Check("dashboard", False, "the page names no built assets")
    seen, bad = 0, []
    queue = list(paths)
    while queue:
        path = queue.pop()
        status, content, kind = http_get(base + path)
        seen += 1
        if status != 200 or not content:
            bad.append(f"{path} {status}")
        elif path.endswith(".js") and "javascript" not in kind:
            bad.append(f"{path} served as {kind}")
        elif path.endswith(".css"):
            queue += [u for u in css_urls(content.decode(errors="replace")) if u not in paths]
            paths += [u for u in queue if u not in paths]
    return Check("dashboard", not bad, f"{seen} files" if not bad else "; ".join(bad[:5]))


def app_token(cfg):
    """The token the running app accepts: its own command, in its own environment."""
    from . import release

    found = release.current(cfg) or Path(cfg.get("AXIOM_BUNDLE")) / "app"
    done = subprocess.run([sys.executable, "-m", "jobpilot.cli", "token"], env=cfg.app_environment(found), cwd=cfg.root,
                          capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
    lines = done.stdout.strip().splitlines()
    return lines[-1] if done.returncode == 0 and lines else ""


def psql_rows(pg, bin_dir, cfg, sql):
    return [line.split("|") for line in pg.psql(bin_dir, cfg, sql, timeout=20).splitlines() if line]


def check_installation(cfg, expected_engine=None, since=None, hold_expected=False):
    """Read-only checks of the running app. `expected_engine` is the engine version of the build that should be running; `since` the
    moment it should have started after (an ISO time): together, proof that the process is the new code and not a survivor."""
    from . import pg, supervisor

    checks = []
    state = supervisor.read_state(cfg)
    children = state.get("children") or {}
    down = [name for name, child in children.items() if child.get("state") not in ("up", "external", "done")]
    checks.append(Check("services", supervisor.supervisor_alive(state) and bool(children) and not down,
                        f"{len(children)} up" if not down else "down: " + ", ".join(down)))
    base = cfg.base_url
    try:
        status, body, _ = http_get(base + "/healthz")
        healthy = status == 200 and json.loads(body).get("status") == "ok"
        checks.append(Check("api", healthy, f"/healthz answered {status}"))
        unauthorized, _, _ = http_get(base + "/api/snapshot")
        checks.append(Check("api auth", unauthorized == 401, f"no token: {unauthorized}"))
    except (OSError, ValueError) as exc:
        checks.append(Check("api", False, type(exc).__name__))
        return checks
    bin_dir = pg.find_bin(cfg)
    remote = pg.remote(cfg)
    if remote:
        checks.append(Check("database", True, "not on this computer: checked through the API"))
    else:
        try:
            one = pg.psql(bin_dir, cfg, "select 1", timeout=10)
            checks.append(Check("database", one == "1", "select 1"))
            present = {}
            for table, column in psql_rows(pg, bin_dir, cfg, "select table_name, column_name from information_schema.columns "
                                                             "where table_schema = 'public'"):
                present.setdefault(table, set()).add(column)
            gaps = schema_gaps(expected_schema(), present)
            checks.append(Check("migrations", not gaps, f"{len(present)} tables" if not gaps else "missing " + ", ".join(gaps[:8])))
            hold = pg.psql(bin_dir, cfg, "select coalesce(value->>'stage','') from settings "
                                         "where key = 'execution_v2_deployment_hold'", timeout=10)
            if not hold_expected and hold in {"draining", "canary"}:
                checks.append(Check("hold", False, f"the deployment hold is {hold}: nothing new can start"))
            started = pg.psql(bin_dir, cfg, "select value->>'engine_version', value->>'at' from settings "
                                            "where key = 'queue_version_recovery'", timeout=10)
            engine, _, at = started.partition("|")
            if expected_engine:
                checks.append(Check("running code", engine == expected_engine and (not since or at >= since),
                                    f"the app started {engine} at {at}; expected {expected_engine} after {since}"))
            heartbeat = pg.psql(bin_dir, cfg, "select value->>'at' from settings where key = 'scheduler_heartbeat'", timeout=10)
            checks.append(Check("database writes", bool(started), "the app recorded its start"))
            checks.append(Check("heartbeat", True, heartbeat or "none yet"))
        except Exception as exc:  # A check that cannot run has failed, and says why.
            checks.append(Check("database", False, f"{type(exc).__name__}: {str(exc)[:200]}"))
    token = app_token(cfg)
    if token:
        status, body, _ = http_get(base + "/api/runtime-health", token)
        if status == 200:
            health = json.loads(body)
            scheduler = health.get("scheduler") or {}
            live = scheduler.get("state") in {"active", "paused"} and not scheduler.get("degraded")
            checks.append(Check("workers", bool(health.get("workers")) and live,
                                f"{health.get('workers')} workers, scheduler {scheduler.get('state')}"))
            checks.append(Check("worker host", not (health.get("host") or {}).get("draining"), "not draining"))
        else:
            checks.append(Check("workers", False, f"/api/runtime-health answered {status}"))
        status, _, _ = http_get(base + "/api/snapshot", token, timeout=30)
        checks.append(Check("api data", status == 200, f"/api/snapshot answered {status}"))
    else:
        checks.append(Check("workers", False, "the app's token could not be read"))
    checks.append(check_assets(base))
    return checks


def run(cfg, args, say=print):
    """`axiom smoke`. Exit status 0 only when every check passed."""
    checks = check_installation(cfg, args.engine or None, args.since or None, args.hold_expected)
    for check in checks:
        say(f"  {'ok  ' if check.ok else 'FAIL'}  {check.name:16} {check.detail}")
    failed = [c.name for c in checks if not c.ok]
    if not args.no_fill:
        say("  ...   dry fill: a job page, Apply, a form and a submit on a site on this computer")
        chromium = cfg.get("CHROMIUM_PATH") or installed_chrome()
        problems = dry_fill(code_path=Path(__file__).resolve().parents[2], chromium=chromium)
        say(f"  {'ok  ' if not problems else 'FAIL'}  {'dry fill':16} " + ("; ".join(problems) or "one dry run, one local submission"))
        failed += ["dry fill"] if problems else []
    return 1 if failed else 0


def installed_chrome():
    """The Chrome a real run uses: what the environment names, else the one in Applications."""
    from .env import system_chrome

    return os.environ.get("CHROMIUM_PATH") or system_chrome()


if __name__ == "__main__":
    command = argparse.ArgumentParser(description="The isolated half of `axiom smoke`")
    command.add_argument("--child", help=argparse.SUPPRESS)
    command.add_argument("--fill", action="store_true", help="Run the dry fill with this Python and these files, and judge it")
    parsed = command.parse_args()
    if parsed.child:
        raise SystemExit(child_main(parsed.child))
    fill_problems = dry_fill(code_path=Path(__file__).resolve().parents[2], chromium=installed_chrome())
    print("; ".join(fill_problems) or "ok: one dry run, one local submission")
    raise SystemExit(1 if fill_problems else 0)
