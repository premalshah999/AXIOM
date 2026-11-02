"""PASS/FAIL for a new install's mail: an empty database, a mailbox connected the way onboarding connects it, and a run
through Service.queue, Service.execute, BrowserEngine.run and a real Chrome that receives and uses a code, a link and a
password reset, in each platform's real format, on each platform's real host (2026-11-01).

Chrome resolves every host to the local site and the mailbox is a real socket speaking Gmail's IMAP (tests/fake_imap.py), so
nothing is faked between the engine and them: the employer's own record says which code, link and reset it took."""

import asyncio
import hashlib
import imaplib
import json
import os
import shutil
import ssl
import tempfile
import threading
import time
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from pypdf import PdfWriter
from sqlalchemy import select

import fake_imap
from jobpilot import browser as browser_module, gmail_imap
from jobpilot.company_sites import allow
from jobpilot.db import Event, Job, MailChallenge, MailRule, Resume, Run
from jobpilot.discovery import add_job
from jobpilot.host_health import is_browser_outage
from jobpilot.schemas import JobInput
from mail_formats import KINDS, ME, PLATFORMS, deliver, fresh_code
from support_budget import scaled
from support_chrome import chrome_opens_a_tab
from test_mail_fresh_install import link_for, onboard

pytestmark = [pytest.mark.browser, pytest.mark.integration]
RUN_SECONDS = float(os.environ.get("MAILFRESH_RUN_SECONDS", "300"))  # a ceiling on a wait for the outcome, paid only by a run that fails (120 s ended two runs "running" beside nine workers, 2026-11-01)
# A real employer's mail takes seconds: longer than the three seconds a run waits in the browser for a link, so the run parks,
# the mail matches, and the run comes back. That is the path real link and reset mail takes.
DELAY = float(os.environ.get("MAILFRESH_DELAY", "8"))
OUTAGE_RETRIES = 2
CHOICES = os.environ.get("MAILFRESH_ONLY", "")
CASES = [
    (p, k)
    for p in PLATFORMS
    for k in KINDS
    if not CHOICES or any(c in {p, k, f"{p}-{k}"} for c in CHOICES.split(","))
]

PAGE = '<!doctype html><html><head><title>{title}</title></head><body><h1>{title}</h1>{body}</body></html>'
CREATE = """<p>Create your candidate account to apply.</p><form method=post action="/account/create">
<label for=e>Email Address</label><input id=e name=email type=email data-automation-id=email required>
<label for=p>Password</label><input id=p name=password type=password data-automation-id=password autocomplete=new-password required>
<label for=q>Verify New Password</label><input id=q name=confirm type=password data-automation-id=verifyPassword autocomplete=new-password required>
<button type=submit data-automation-id=createAccountSubmitButton>Create Account</button></form><p><a href="/signin" data-automation-id=signInLink>Already have an account? Sign in</a></p>"""
SIGNIN = """{message}<form method=post action="/signin">
<label for=e>Email Address</label><input id=e name=email type=email data-automation-id=email required>
<label for=p>Password</label><input id=p name=password type=password data-automation-id=password required>
<button type=submit data-automation-id=signInSubmitButton>Sign In</button></form><p><a href="/password/forgot">Forgot your password?</a></p>
<p><a href="/account/create" data-automation-id=createAccountLink>Create Account</a></p>"""
FORGOT = """<p>Forgot your password? Enter your email and we will send a reset link.</p><form method=post action="/password/forgot">
<label for=e>Email Address</label><input id=e name=email type=email data-automation-id=email required>
<button type=submit data-automation-id=resetPasswordButton>Reset Password</button></form><p><a href="/signin" data-automation-id=signInLink>Sign in</a></p>"""
CODE = """<p>Enter the verification code we emailed you.</p>{message}<form method=post action="/verify-code">
<label for=c>Verification code</label><input id=c name=code autocomplete=one-time-code required>
<button type=submit>Verify</button></form>"""
CHECK = "<p>Check your email. We sent you a verification link. Open it to continue your application.</p>"
SENT = "<p>Check your email. If an account exists we sent a link to reset your password.</p>"
NEW = """<form method=post action="/password/new">
<label for=p>New Password</label><input id=p name=password type=password autocomplete=new-password required>
<label for=q>Confirm New Password</label><input id=q name=confirm type=password autocomplete=new-password required>
<button type=submit>Save Password</button></form>"""
FORM = """<h2>My Information</h2><form method=post action="/applications" enctype="multipart/form-data">
<label for=f>First Name</label><input id=f name=first_name required>
<label for=l>Last Name</label><input id=l name=last_name required>
<label for=m>Email</label><input id=m name=email type=email required>
<label for=r>Resume</label><input id=r name=resume type=file required>
<button type=submit>Submit Application</button></form>"""
DONE = "<p>Thank you for applying. Your application has been received.</p>"


def certificate(folder):
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, "local-employer")])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=2)).sign(key, hashes.SHA256())
    )
    path = os.path.join(folder, "site.pem")
    with open(path, "wb") as f:
        f.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        f.write(cert.public_bytes(serialization.Encoding.PEM))
    return path


class Employer:
    """One employer's site. `used` is its own record of what it took; `kind` is which mail its account step sends."""

    def __init__(self, platform, kind, box, delay=DELAY, other=False, serve=True):
        self.platform, self.kind, self.box, self.delay, self.other = platform, kind, box, delay, other
        self.spec = {**PLATFORMS[platform], **PLATFORMS[platform]["other"]} if other else PLATFORMS[platform]
        self.tenants = {}  # Host -> another employer this server answers for.
        self.used, self.sent, self.posts, self.hits = set(), [], [], []
        self.verified, self.code, self.link, self.password = False, None, None, "the-password-only-the-applicant-forgot"
        self.expected_email = ME
        if not serve:
            return
        folder = tempfile.mkdtemp(prefix="mailfresh-")
        self.folder = folder
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def reply(self, title, body, status=200, location=None):
                page = PAGE.format(title=title, body=body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(page)))
                if location:
                    self.send_header("Location", location)
                self.end_headers()
                self.wfile.write(page)

            def do_GET(self):
                outer.tenants.get(self.headers.get("Host", "").split(":")[0], outer).get(self)

            def do_POST(self):
                size = int(self.headers.get("Content-Length") or 0)
                outer.tenants.get(self.headers.get("Host", "").split(":")[0], outer).post(self, self.rfile.read(size))

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(certificate(folder))
        self.server.socket = context.wrap_socket(self.server.socket, server_side=True)
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    def mail(self, kind):
        """The employer mails `delay` seconds after it was asked."""
        if kind == "code":
            self.code = fresh_code()
        else:
            self.link = link_for(self.spec, kind)
        self.sent.append(kind)
        timer = threading.Timer(self.delay, lambda: deliver(self.box, self.platform, kind, code=self.code, link=self.link, other=self.other))
        timer.daemon = True
        timer.start()

    def get(self, h):
        parts = urlsplit(h.path)
        path, query = parts.path, parse_qs(parts.query)
        host = (h.headers.get("Host") or "").split(":")[0]
        self.hits.append(path)
        if host.endswith(("tracking.icims.com", "clicks.icims.com")):
            # iCIMS's click tracker: it answers with where the button really goes.
            target = self.spec["url"].split("/jobs")[0] + ("/verify" if self.kind == "link" else "/password/reset")
            self.used_tracker = True
            token = "tok-tracked"
            self.link_token = token
            return h.reply("Redirecting", "", 302, f"{target}?token={token}")
        if path == "/verify":
            given = query.get("token", [""])[0]
            if self.link and (given in self.link or given == getattr(self, "link_token", None)):
                self.used.add("link")
                self.verified = True
                return h.reply("Your email has been verified", FORM)
            return h.reply("This link has expired", "<p>Please ask for a new link.</p>")
        if path == "/password/reset":
            given = query.get("token", [""])[0]
            if self.link and (given in self.link or given == getattr(self, "link_token", None)):
                return h.reply("Set a new password", NEW)
            return h.reply("This link has expired", "<p>Please ask for a new link.</p>")
        if path == "/password/forgot":
            return h.reply("Forgot your password?", FORGOT)
        if path == "/signin":
            return h.reply("Sign In", SIGNIN.format(message=""))
        if path == "/account/create":
            return h.reply("Create Account", CREATE)
        if self.verified:
            return h.reply("Application", FORM)
        if self.kind == "reset":
            return h.reply("Sign In", SIGNIN.format(message=""))
        return h.reply("Create Account", CREATE)

    def post(self, h, raw):
        path = urlsplit(h.path).path
        self.posts.append(path)
        form = {} if path == "/applications" else {k: v[-1] for k, v in parse_qs(raw.decode("utf-8", "replace")).items()}
        if path == "/account/create":
            if self.kind == "reset":
                return h.reply("Sign In", SIGNIN.format(message="<p role=alert>An account with this email already exists. Please sign in.</p>"))
            self.mail(self.kind)
            if self.kind == "code":
                return h.reply("Verify your email", CODE.format(message=""))
            if self.platform == "workday":
                # Workday returns to Sign In with a banner; the account is pending until its link is opened.
                return h.reply("Sign In", SIGNIN.format(message="<p role=alert>Please verify your account: check your email.</p>"))
            return h.reply("Check your email", CHECK)
        if path == "/verify-code":
            if form.get("code") == self.code:
                self.used.add("code")
                self.verified = True
                return h.reply("Application", FORM)
            self.wrong_code = form.get("code")
            return h.reply("Verify your email", CODE.format(message="<p role=alert>That code is not correct.</p>"))
        if path == "/signin":
            if form.get("password") == self.password:
                self.verified = True
                if "new-password" in self.sent:
                    self.used.add("reset")
                return h.reply("Application", FORM)
            return h.reply("Sign In", SIGNIN.format(message="<p role=alert>Invalid email or password.</p>"))
        if path == "/password/forgot":
            self.mail("reset")
            return h.reply("Check your email", SENT)
        if path == "/password/new":
            self.password = form.get("password")
            self.sent.append("new-password")
            self.used.add("reset")
            self.verified = True
            return h.reply("Your password has been reset", "<p>Your password has been reset.</p><p><a href='/signin'>Sign In</a></p>")
        if path == "/applications":
            return h.reply("Thank you", DONE)
        return h.reply("Not found", "", 404)


def blank_pdf():
    from io import BytesIO

    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    out = BytesIO()
    writer.write(out)
    return out.getvalue()


@pytest.fixture
def imap(monkeypatch):
    server, box = fake_imap.start()
    port = server.server_address[1]
    monkeypatch.setattr(imaplib, "IMAP4_SSL", lambda host, p, **kw: imaplib.IMAP4("127.0.0.1", port, timeout=kw.get("timeout")))
    for cache in (gmail_imap._clients, gmail_imap._where, gmail_imap._identity):
        cache.clear()
    yield box
    server.shutdown()
    server.server_close()
    gmail_imap._clients.clear()


async def start_chrome(temp, port):
    """The worker Chrome a new install attaches to, with every host mapped to the local employer. The tab stays open while a
    run is parked on its mail, as it does in the app."""
    executable = os.environ.get("CHROMIUM_PATH") or ""
    if not executable:
        from playwright.async_api import async_playwright

        pw = await async_playwright().start()
        executable = pw.chromium.executable_path
        await pw.stop()
    argv = browser_module.throwaway_chrome(executable, temp, True)
    process = await asyncio.create_subprocess_exec(
        *argv, f"--host-resolver-rules=MAP * 127.0.0.1:{port}", "--ignore-certificate-errors", "about:blank",
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
    )
    portfile = os.path.join(temp, "DevToolsActivePort")
    for _ in range(3000):  # until Chrome has written its port: ten seconds failed on a loaded Mac
        if os.path.exists(portfile) and os.path.getsize(portfile):
            break
        await asyncio.sleep(0.1)
    with open(portfile) as f:
        address = f"http://127.0.0.1:{f.read().splitlines()[0]}"
    await chrome_opens_a_tab(address, scaled(RUN_SECONDS))
    return process, address


def outage(service, job_id):
    """The job's last run ended "AXIOM's browser wasn't ready": nothing was sent, and the app tries again by itself. The autopilot
    that does that isn't running here, so the test presses Apply again, at most OUTAGE_RETRIES times; the third outage in a row
    pauses the app's starts (host_health.note_browser), so a browser that never comes up still fails the test."""
    with service.db.session() as s:
        last = s.scalars(select(Run).where(Run.job_id == job_id).order_by(Run.created_at.desc()).limit(1)).first()
        return bool(last and is_browser_outage(last.state, last.reason))


async def one_run(client, service, imap, platform, kind, *, delay=DELAY, profile=None):
    from jobpilot.outbox import poll as poll_outbox
    from jobpilot.owner_apply import apply

    config = service.config
    spec = PLATFORMS[platform]
    config.repair_agent_enabled = False
    config.mimo_api_key = "no-model"
    config.mimo_base_url = "http://127.0.0.1:9/v1"
    config.mail_poll_seconds = 0.5
    config.headless = True
    with service.db.session() as s:
        assert not list(s.scalars(select(MailRule)))  # nothing learned
    onboard(client)
    pdf = blank_pdf()
    (config.data_dir / "resumes").mkdir(exist_ok=True)
    (config.data_dir / "resumes" / "alex.pdf").write_bytes(pdf)
    with service.db.exclusive() as s:
        resume = Resume(name="Alex", filename="alex.pdf", sha256=hashlib.sha256(pdf).hexdigest(), text="Alex Rivera, analyst.")
        s.add(resume)
    # What the onboarding screens save for someone who leaves "apply on its own" off: only the permission to make accounts
    # and accept terms is turned on.
    service.db.set_setting("profile", {"name": "Alex Rivera", "email": ME, "first_name": "Alex", "last_name": "Rivera",
                                       "autonomous": False, "allow_account_creation": True, "accept_all_application_terms": True,
                                       "facts": {"first_name": "Alex", "last_name": "Rivera"}, **(profile or {})})
    service.db.set_setting("control", {"paused": False, "auto_submit": True})
    tasks = set()

    async def dispatch(run_id):
        task = asyncio.create_task(service.execute(run_id))
        tasks.add(task)
        task.add_done_callback(tasks.discard)

    service.dispatch = dispatch
    site = Employer(platform, kind, imap, delay=delay)
    temp = tempfile.mkdtemp(prefix="mailfresh-chrome-")
    process, config.browser_cdp_url = await start_chrome(temp, site.port)
    config_workers = getattr(config, "workers", None)
    job, _ = add_job(service.db, JobInput(url=spec["url"], title="Analyst", company=spec["company"], location="Remote", description="A synthetic posting."))
    with service.db.exclusive() as s:
        row = s.get(Job, job["id"])
        row.status, row.ats = "ready", spec["ats"]
    allow(service.db, [job["id"]])  # The Apply button on a company's own site is the owner's click (company_sites.py).
    outcome = {"state": "not started", "reason": ""}
    poller = asyncio.create_task(service.mail.poll())
    service.outbox_running = True
    outbox = asyncio.create_task(poll_outbox(service))
    try:
        result = await apply(service, job["id"])  # The Apply button.
        outcome["applied"] = str(result)[:120]
        deadline = time.monotonic() + scaled(RUN_SECONDS)
        retries = OUTAGE_RETRIES
        while time.monotonic() < deadline:
            await asyncio.sleep(0.25)  # a poll of the database, not a pace: the run ends within a quarter second of finishing
            with service.db.session() as s:
                states = [r.state for r in s.scalars(select(Run).where(Run.job_id == job["id"]))]
            live = any(x in {"queued", "running", "submitting"} for x in states) or tasks
            parked = states and states[-1] in {"waiting_email"}
            if not live and retries and outage(service, job["id"]):
                retries -= 1
                await apply(service, job["id"])
                continue
            if kind in site.used and not live:
                break
            if states and not live and not parked:
                await asyncio.sleep(2)
                with service.db.session() as s:
                    again = [r.state for r in s.scalars(select(Run).where(Run.job_id == job["id"]))]
                if len(again) == len(states) and not tasks and not any(x in {"queued", "running", "submitting"} for x in again):
                    break
    except Exception as exc:  # The table reports it; the assertion below fails with it.
        outcome["reason"] = f"{type(exc).__name__}: {str(exc)[:200]}"
    finally:
        service.outbox_running = False
        for task in (poller, outbox, *tasks):
            task.cancel()
        await asyncio.gather(poller, outbox, *tasks, return_exceptions=True)
        site.close()
        process.terminate()
        await process.wait()
        shutil.rmtree(temp, ignore_errors=True)
    with service.db.session() as s:
        rows = list(s.scalars(select(Run).where(Run.job_id == job["id"]).order_by(Run.created_at)))
        if rows:
            outcome.update(state=rows[-1].state, reason=rows[-1].reason or outcome["reason"], runs=[r.state for r in rows])
        events = [(e.kind, e.message, e.data) for e in s.scalars(select(Event).order_by(Event.id))]
        challenges = [(c.kind, c.state, c.reason) for c in s.scalars(select(MailChallenge))]
        rules = [(r.employer_origin, r.sender_domains, r.link_origins) for r in s.scalars(select(MailRule))]
    return site, outcome, events, challenges, rules


@pytest.mark.parametrize("platform,kind", CASES)
async def test_a_new_install_receives_and_uses_the_mail(client, service, imap, platform, kind):
    site, outcome, events, challenges, rules = await one_run(client, service, imap, platform, kind)
    trail = "\n".join(f"  {k}: {str(m)[:120]}" for k, m, _ in events[-30:])
    polls = [d for k, _, d in events if k == "mail_poll"]
    why = (
        f"{platform}/{kind} ended {outcome['state']}: {outcome['reason'][:220]}\nused={site.used} sent={site.sent} posts={site.posts}\n"
        f"challenges={challenges}\nrules={rules}\npolls={json.dumps(polls)[:800]}\n{trail}"
    )
    if path := os.environ.get("MAILFRESH_REPORT"):
        with open(path, "a") as f:
            f.write(json.dumps({"platform": platform, "kind": kind, "outcome": "ok" if kind in site.used else "FAIL",
                                "state": outcome["state"], "reason": outcome["reason"][:160], "sent": site.sent,
                                "runs": outcome.get("runs"), "challenges": [c[:2] for c in challenges], "rules": len(rules)}) + "\n")
    assert kind in site.used, why
    if kind == "code":
        # The code that worked finished the account: Settings must not go on saying it needs email verification.
        from jobpilot.accounts.store import AccountStore

        states = {AccountStore(service, ME).state(PLATFORMS[platform]["url"])}
        assert states <= {"authenticated"}, (platform, states)


@pytest.mark.parametrize("platform", ["icims", "oracle"])
async def test_two_employers_on_one_shared_sender_in_two_real_runs_each_type_their_own_code(client, service, imap, platform):
    """Two real runs at once on one platform's sender. Both employers' codes land within a second of each other; each site
    records the code it took, and a wrong code would be on its own record."""
    from jobpilot.outbox import poll as poll_outbox
    from jobpilot.owner_apply import apply

    config = service.config
    spec = PLATFORMS[platform]
    config.repair_agent_enabled = False
    config.mimo_api_key = "no-model"
    config.mimo_base_url = "http://127.0.0.1:9/v1"
    config.mail_poll_seconds = 0.5
    config.headless = True
    onboard(client)
    pdf = blank_pdf()
    (config.data_dir / "resumes").mkdir(exist_ok=True)
    (config.data_dir / "resumes" / "alex.pdf").write_bytes(pdf)
    with service.db.exclusive() as s:
        s.add(Resume(name="Alex", filename="alex.pdf", sha256=hashlib.sha256(pdf).hexdigest(), text="Alex Rivera, analyst."))
    service.db.set_setting("profile", {"name": "Alex Rivera", "email": ME, "first_name": "Alex", "last_name": "Rivera",
                                       "autonomous": False, "allow_account_creation": True, "accept_all_application_terms": True,
                                       "facts": {"first_name": "Alex", "last_name": "Rivera"}})
    service.db.set_setting("control", {"paused": False, "auto_submit": True})
    tasks = set()

    async def dispatch(run_id):
        task = asyncio.create_task(service.execute(run_id))
        tasks.add(task)
        task.add_done_callback(tasks.discard)

    service.dispatch = dispatch
    first = Employer(platform, "code", imap, delay=4)
    second = Employer(platform, "code", imap, delay=4, other=True, serve=False)
    first.tenants[urlsplit(second.spec["url"]).hostname] = second
    temp = tempfile.mkdtemp(prefix="mailfresh-chrome-")
    process, config.browser_cdp_url = await start_chrome(temp, first.port)
    jobs = []
    for site in (first, second):
        job, _ = add_job(service.db, JobInput(url=site.spec["url"], title="Analyst", company=site.spec["company"], location="Remote", description="A synthetic posting."))
        with service.db.exclusive() as s:
            row = s.get(Job, job["id"])
            row.status, row.ats = "ready", spec["ats"]
        jobs.append(job["id"])
    poller = asyncio.create_task(service.mail.poll())
    service.outbox_running = True
    outbox = asyncio.create_task(poll_outbox(service))
    try:
        for job_id in jobs:
            await apply(service, job_id)
        deadline = time.monotonic() + scaled(RUN_SECONDS)
        retries = dict.fromkeys(jobs, OUTAGE_RETRIES)
        while time.monotonic() < deadline and not ("code" in first.used and "code" in second.used):
            await asyncio.sleep(1)
            # A platform that runs one application at a time defers the second: the autopilot starts it when its turn comes.
            with service.db.session() as s:
                waiting = [j for j in jobs if not s.scalar(select(Run.id).where(Run.job_id == j).limit(1))]
            for job_id in jobs:
                if retries[job_id] and outage(service, job_id):
                    retries[job_id] -= 1
                    waiting.append(job_id)
            for job_id in waiting:
                await apply(service, job_id)
    finally:
        service.outbox_running = False
        for task in (poller, outbox, *tasks):
            task.cancel()
        await asyncio.gather(poller, outbox, *tasks, return_exceptions=True)
        first.close()
        process.terminate()
        await process.wait()
        shutil.rmtree(temp, ignore_errors=True)
    with service.db.session() as s:
        states = [(r.state, (r.reason or "")[:90]) for r in s.scalars(select(Run))]
        windows = [(c.kind, c.state, c.reason[:70]) for c in s.scalars(select(MailChallenge))]
    # The employer's own record: neither ever saw a code that was not its own.
    assert not getattr(first, "wrong_code", None) and not getattr(second, "wrong_code", None), (states, windows)
    assert "code" in first.used and "code" in second.used, (first.sent, second.sent, states, windows)
