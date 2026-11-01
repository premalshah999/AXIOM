"""Two causes from reading the saved pages of SuccessFactors, Eightfold and Avature runs (2026-10-25..11-01), each driven
through the real adapter methods in a real Chromium against pages served from this machine, ending at a local submit.

1. A site's own page (Lenovo's Avature home after a password reset, Electronic Arts' /SearchJobs, ASSA ABLOY's candidate
   portal) asks nothing and offers no way forward. It classified as the form, the form handler found nothing, and the run
   went to the model with "No way forward from this page" (85 pages, 20 runs). A person goes back to the job.
2. The page behind an emailed reset link (Lenovo, 2026-11-01): its Next is disabled until the new password passes the page's
   checks, so the scan taken before typing had no submit control and the run stopped with a "control failure"; the shared
   password is also one of the account's last three, which the page forbids."""

import http.server
import threading
import urllib.parse
from types import SimpleNamespace

import pytest

from jobpilot.accounts import Accounts
from jobpilot.adapters.base import Adapter, Step
from jobpilot.answers import Resolver
from jobpilot.forms import FormSession
from jobpilot.schemas import Profile

HOME = """<!doctype html><title>Careers</title><a href="/JobDetail?jobId=7">Featured role</a>
<label for=c>Country/Region</label><input id=c type=text>
<label for=a>Career Area</label><input id=a type=text><button type=button>Search jobs</button>"""
JOB = """<!doctype html><title>Role</title><h1>Software Engineer</h1><a href="/Application?jobId=7"><button type=button>Apply</button></a>"""
APPLICATION = """<!doctype html><title>Apply</title><form method=post action="/submit">
<label for=f>First name *</label><input id=f name=first required>
<label for=l>Last name *</label><input id=l name=last required>
<label for=e>Email *</label><input id=e name=email type=email required>
<button type=submit>Continue</button></form>"""
RESET = """<!doctype html><title>Reset</title><h1>Hello!</h1>
<p>Please set up your new credentials in order to log in. Your new password must: have at least 8 characters, have at least one
number, be different from your previous 3 passwords.</p>
<form method=post action="/reset"><label for=p>New password</label><input id=p name=password type=password>
<label for=q>Confirm new password</label><input id=q name=confirm type=password>
<button type=submit id=go disabled>Next</button></form>
<script>const p=document.getElementById('p'),q=document.getElementById('q'),go=document.getElementById('go');
function check(){go.disabled=!(p.value.length>=8&&/[0-9]/.test(p.value)&&p.value===q.value)}
p.addEventListener('input',check);q.addEventListener('input',check);</script>"""
DONE = "<!doctype html><title>Done</title><p>Your password was changed. You can close this page.</p>"
TOO_OLD = "<!doctype html><title>Reset</title><p>The password doesn't meet the security requirements.</p>"


class Site(http.server.ThreadingHTTPServer):
    def __init__(self):
        super().__init__(("127.0.0.1", 0), Handler)
        self.posts, self.history = [], []
        threading.Thread(target=self.serve_forever, daemon=True).start()

    @property
    def base(self):
        return f"http://127.0.0.1:{self.server_address[1]}"


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, body, status=200):
        data = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = urllib.parse.urlsplit(self.path).path
        self.reply({"/careers": HOME, "/JobDetail": JOB, "/Application": APPLICATION, "/reset-link": RESET}.get(path, "?"))

    def do_POST(self):
        body = urllib.parse.parse_qs(self.rfile.read(int(self.headers.get("Content-Length", 0))).decode())
        self.server.posts.append((self.path, {k: v[0] for k, v in body.items()}))
        if self.path == "/reset":
            self.reply(TOO_OLD if body["password"][0] in self.server.history else DONE)
        else:
            self.reply("<!doctype html><title>Thanks</title><p>Received.</p>")


@pytest.fixture
def site():
    server = Site()
    yield server
    server.shutdown()
    server.server_close()


def adapter_on(service, page, site, tmp_path, job_path, first_url):
    """The real Adapter on a real FormSession and page; only the engine around it is a stub."""
    resume = tmp_path / "resume.pdf"
    resume.write_bytes(b"%PDF-1.4")
    profile = Profile(first_name="Alex", last_name="Rivera", email="alex@example.test", allow_account_creation=True,
                      accept_all_application_terms=True)
    form = FormSession(page, Resolver(profile, service.db, service.config), resume, lambda *a: None)
    events = []
    engine = SimpleNamespace(
        service=service, config=service.config, form=form, page=page, profile=profile, run_record={"packet": {}},
        job={"id": "job", "url": site.base + job_path, "ats": "custom", "company": "Example"}, result=None,
        emit=lambda kind, message, data=None: events.append((kind, message)),
    )
    value = Adapter(engine, "custom", "Career site")
    value.events = events
    return value, first_url


async def test_a_site_page_after_sign_in_sends_the_run_back_to_the_job_and_through_to_a_local_submit(
    page, service, site, tmp_path
):
    adapter, home = adapter_on(service, page, site, tmp_path, "/JobDetail?jobId=7", None)
    await page.goto(site.base + "/careers")
    obs = await adapter.form.scan()
    assert adapter.classify_page(obs) == Step.FORM  # the page's own search filters read as the form (the cause)
    for _ in range(6):
        obs = await adapter.form.scan()
        step = adapter.classify_page(obs)
        if step == Step.JOB:
            result = await adapter.on_job_details(obs)
        else:
            assert step == Step.FORM
            if page.url.endswith("/Application?jobId=7"):
                report = await adapter.fill_page()
                assert not (isinstance(report, dict) and report.get("state")), report
                await adapter.click(adapter.find_next(await adapter.form.scan()))
                await page.wait_for_load_state()
                break
            result = await adapter.on_form(obs)
        assert not (isinstance(result, dict) and result.get("fallback")), result  # never "No way forward"
    assert site.posts == [("/submit", {"first": "Alex", "last": "Rivera", "email": "alex@example.test"})]
    assert any("back to the job" in m for _, m in adapter.events)


async def test_the_return_is_made_once(page, service, site, tmp_path):
    adapter, _ = adapter_on(service, page, site, tmp_path, "/JobDetail?jobId=7", None)
    await page.goto(site.base + "/careers")
    obs = await adapter.form.scan()
    assert await adapter.leave_site_page(obs)
    await page.goto(site.base + "/careers")
    assert not await adapter.leave_site_page(await adapter.form.scan())  # a second time the page is what it is


async def test_a_page_with_a_required_question_is_never_left(page, service, site, tmp_path):
    adapter, _ = adapter_on(service, page, site, tmp_path, "/JobDetail?jobId=7", None)
    await page.goto(site.base + "/Application?jobId=7")
    assert not await adapter.leave_site_page(await adapter.form.scan())
    assert page.url.endswith("/Application?jobId=7")


async def test_a_reset_page_whose_next_is_disabled_until_typed_takes_a_password_it_has_not_seen(
    page, service, site, tmp_path
):
    adapter, _ = adapter_on(service, page, site, tmp_path, "/careers", None)
    shared = adapter.accounts.store.shared()["password"]
    site.history.append(shared)  # an earlier reset already made it the account's password
    await page.goto(site.base + "/reset-link")
    obs = await adapter.form.scan()
    assert adapter.classify_page(obs) == Step.NEW_PASSWORD
    assert adapter.auth_button(obs, "reset") is None  # Next is disabled: the scan before typing has no control
    result = await adapter.set_new_password(adapter.auth_url(obs), obs)  # the emailed link's route, not the forced sign-in's
    assert result is None, result
    (path, body), = site.posts
    assert path == "/reset" and body["password"] == body["confirm"] and body["password"] != shared
    assert adapter.accounts.saved(adapter.auth_url(obs))["password"] == body["password"]  # kept only after acceptance
