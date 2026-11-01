"""One real-engine application against a local copy of an ATS page, in a real headless Chrome, with a model that cannot answer.

usage: python scripts/bench/page_speed/run.py <greenhouse|workday|successfactors> <data_dir> [--dry]

The page is served over HTTPS by this process under the ATS's own host name (Chrome is told to resolve that name to 127.0.0.1), so
the engine's per-ATS code paths run exactly as on the real site. The model's address is a closed port: a run that needs a model
answer fails, which is what keeps these runs honest (every answer comes from the saved profile). Prints one `RESULT {json}` line:
the run's state, seconds, what the site received, and the engine's own per-page timing events. Synthetic data only.
"""

import asyncio
import hashlib
import http.server
import json
import os
import ssl
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent.parent))
sys.path.insert(0, str(HERE.parent))

SITE, DATA = sys.argv[1], Path(sys.argv[2])
MODE = "dry_run" if "--dry" in sys.argv else "submit"

from page_speed import greenhouse, successfactors, workday  # noqa: E402

SITES = {
    "greenhouse": (greenhouse, "job-boards.greenhouse.io"),
    "workday": (workday, "examplesoft.wd5.myworkdayjobs.com"),
    "successfactors": (successfactors, "career5.successfactors.com"),
}
MODULE, HOST = SITES[SITE]


class Site:
    def __init__(self):
        self.received = []

    def record(self, name, body):
        self.received.append({"site": name, "bytes": len(body)})


def serve(site):
    class H(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_):
            pass

        def answer(self, method):
            body = b""
            if method == "POST":
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            parts = urlsplit(self.path)
            status, html = MODULE.serve(site, parts.path + (("?" + parts.query) if parts.query else ""), method, body)
            payload = html.encode() if isinstance(html, str) else html
            kind = "application/json" if html.startswith("{") else "text/html; charset=utf-8"
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self):
            self.answer("GET")

        def do_POST(self):
            self.answer("POST")

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    work = Path(tempfile.mkdtemp())
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", str(work / "k.pem"), "-out", str(work / "c.pem"), "-days", "2", "-subj", f"/CN={HOST}"],
        check=True,
        capture_output=True,
    )
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(work / "c.pem", work / "k.pem")
    srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv.server_address[1]


PROFILE = {
    "name": "Alex Example",
    "email": "alex@example.test",
    "phone": "3015550100",
    "autonomous": True,
    "accept_all_application_terms": True,
    "address": {"line1": "100 Example St", "city": "Baltimore", "state": "MD", "postal_code": "21201", "country": "United States"},
    "facts": {
        "first_name": "Alex", "last_name": "Example", "phone": "3015550100", "country": "United States", "city": "Baltimore", "state": "Maryland", "location": "Baltimore, MD",
        "zip": "21201", "address": "100 Example St", "linkedin": "https://www.linkedin.com/in/alex-example",
        "work_authorized_us": "Yes", "requires_sponsorship": "No", "gender": "Male", "veteran_status": "I am not a protected veteran",
        "disability_status": "No, I do not have a disability and have not had one in the past", "race": "Asian (Not Hispanic or Latino)",
        "ethnicity": "Asian", "hispanic": "No", "heard_about": "Job board",
    },
    "education": [{"school": "Example University", "degree": "Bachelor's Degree", "field_of_study": "Computer Science", "start": "2016-08", "end": "2020-05", "gpa": "3.6"}],
    "work": [{"company": "Sample Analytics", "title": "Data Analyst", "start": "2020-06", "end": "", "current": True, "description": "Built reports."}],
}


async def main():
    from sqlalchemy import select

    from jobpilot import browser as B
    from jobpilot.config import Settings
    from jobpilot.db import Database, Event, Job, Resume, Run
    from jobpilot.discovery import add_job
    from jobpilot.schemas import JobInput
    from jobpilot.service import Service

    profile = {}
    if "--profile" in sys.argv:
        import functools
        import inspect

        from jobpilot import forms, widgets

        def wrap(owner, name, fn):
            @functools.wraps(fn)
            async def inner(*a, **k):
                t = time.monotonic()
                try:
                    return await fn(*a, **k)
                finally:
                    e = profile.setdefault(name, [0, 0.0])
                    e[0] += 1
                    e[1] += time.monotonic() - t

            setattr(owner, name, inner)

        for name, fn in list(vars(widgets).items()):
            if inspect.iscoroutinefunction(fn) and fn.__module__ == widgets.__name__:
                wrap(widgets, "w." + name, fn) if False else None
                profile_name = "w." + name

                def make(fn=fn, profile_name=profile_name):
                    @functools.wraps(fn)
                    async def inner(*a, **k):
                        t = time.monotonic()
                        try:
                            return await fn(*a, **k)
                        finally:
                            e = profile.setdefault(profile_name, [0, 0.0])
                            e[0] += 1
                            e[1] += time.monotonic() - t

                    return inner

                setattr(widgets, name, make())
        for name, fn in list(vars(forms.FormSession).items()):
            if inspect.iscoroutinefunction(fn):

                def make2(fn=fn, profile_name="f." + name):
                    @functools.wraps(fn)
                    async def inner(*a, **k):
                        t = time.monotonic()
                        try:
                            return await fn(*a, **k)
                        finally:
                            e = profile.setdefault(profile_name, [0, 0.0])
                            e[0] += 1
                            e[1] += time.monotonic() - t

                    return inner

                setattr(forms.FormSession, name, make2())
    site = Site()
    port = serve(site)
    orig = B.throwaway_chrome

    def patched(executable, profile, headless, platform=sys.platform):
        args = orig(executable, profile, headless, platform)
        return args[:1] + [f"--host-resolver-rules=MAP {HOST} 127.0.0.1", "--ignore-certificate-errors"] + args[1:]

    B.throwaway_chrome = patched
    DATA.mkdir(parents=True, exist_ok=True)
    config = Settings(
        _env_file=None, database_url="", dbos_database_url="", data_dir=DATA, app_token="bench", enable_workers=False,
        allow_private_urls=True, answer_quality_enabled=False, browser_vision_recovery=False, failure_reports_enabled=False,
        browser_cdp_url="", chromium_path=os.environ.get("CHROMIUM_PATH", ""), headless=True, repair_agent_enabled=False,
        mimo_api_key="no-model-is-called", mimo_base_url="http://127.0.0.1:9/v1", llm_provider="mimo",
    ).prepare()
    service = Service(Database(config.database_url), config)

    async def dispatch(_r):
        pass

    service.dispatch = dispatch
    from jobpilot.native.smoke import blank_pdf

    pdf = blank_pdf()
    (config.data_dir / "resumes").mkdir(exist_ok=True)
    (config.data_dir / "resumes" / "bench.pdf").write_bytes(pdf)
    with service.db.exclusive() as s:
        r = Resume(name="Bench", filename="bench.pdf", sha256=hashlib.sha256(pdf).hexdigest(), text="Alex Example, data analyst. Sample Analytics 2020-present.")
        s.add(r)
        s.flush()
        rid = r.id
    service.db.set_setting("profile", PROFILE)
    service.db.set_setting("control", {"paused": False, "auto_submit": True})
    url = f"https://{HOST}:{port}{MODULE.JOB_URL}"
    job, _ = add_job(service.db, JobInput(url=url, title="Data Analyst", company="ExampleSoft", location="Remote", description="A synthetic posting."))
    with service.db.exclusive() as s:
        s.get(Job, job["id"]).status = "ready"
    run = await service.queue(job["id"], MODE, rid, owner_pick=True)
    started = time.monotonic()
    try:
        await asyncio.wait_for(service.execute(run["id"]), 240)
    except TimeoutError:
        pass
    wall = time.monotonic() - started
    service.db.flush_events()
    with service.db.session() as s:
        row = s.get(Run, run["id"])
        events = s.scalars(select(Event).where(Event.run_id == run["id"]).order_by(Event.id)).all()
        timing = [e.data for e in events if e.kind == "page_timing"]
        slow = [e.data for e in events if e.kind == "field_timing"]
        settles = [e.data for e in events if e.kind == "settle_timing"]
        out = dict(
            site=SITE, state=row.state, reason=(row.reason or "")[:160], wall=round(wall, 2), elapsed=round(row.elapsed or 0, 2),
            received=len(site.received), model_calls=row.model_calls, pages=timing, slow_fields=slow, settles=settles,
            errors=[(e.message, (e.data or {}).get("safe_detail") or (e.data or {}).get("error_type")) for e in events if e.kind in {"field_error", "observation_failure"}][:8],
            kinds=[e.kind for e in events],
            offsets=[(e.kind, round((e.created_at - events[0].created_at).total_seconds(), 1) if hasattr(e.created_at, "year") else 0) for e in events if e.kind in {"browser", "form_page", "verification", "submitting", "finished"}],
            profile={k: [v[0], round(v[1], 2)] for k, v in sorted(profile.items(), key=lambda kv: -kv[1][1])[:45]},
        )
    print("RESULT " + json.dumps(out))


asyncio.run(main())
