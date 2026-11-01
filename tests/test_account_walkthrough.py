"""Account steps driven through the real Adapter in a real browser against pages this test hosts (wave 12, accounts2).

Each case is an account stop seen on live employers: the page is a synthetic stand-in served over HTTP, the adapter,
form session, resolver and account store are the real ones. Only the mailbox is faked (there is no real inbox here):
it hands back the link the page's "email" would carry."""

import json
import threading
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

from jobpilot.adapters.base import Adapter, create_allowed, create_stamp
from jobpilot.answers import Resolver
from jobpilot.forms import FormSession
from jobpilot.schemas import Profile

GUEST_PAGE = """<!doctype html><html><head><title>Create a User Name and Password</title></head><body>
<h3>Create a User Name and Password</h3>
<p>The email address provided (alex@example.test) already exists and cannot be used to create a guest submission.</p>
<p>To apply using this email address, create a username and password.</p>
<p>Please click Send Email to receive a link and instructions.</p>
<form method=post action="/send"><input type=submit value="Send Email" id=send></form>
<form method=get action="/login"><input type=submit value="Cancel"></form></body></html>"""

LOGIN_PAGE = """<!doctype html><html><body><h3>Applicant Login</h3>
<form method=post action="/signin"><label for=u>User Name *</label><input id=u name=u type=text>
<label for=p>Password</label><input id=p name=p type=password>
<input type=submit value="Login"></form><a href="/forgot">FORGOT YOUR PASSWORD?</a></body></html>"""

CREATE_PAGE = """<!doctype html><html><body><h3>Create a User Name and Password</h3>
<form method=post action="/create"><label for=u>User Name *</label><input id=u name=u type=text>
<label for=p>New Password *</label><input id=p name=p type=password>
<label for=q>Confirm Password *</label><input id=q name=q type=password>
<input type=submit value="Submit"></form></body></html>"""

DONE_PAGE = "<!doctype html><html><body><h3>Your account was created.</h3><a href='/login'>Continue</a></body></html>"

GIGYA_PAGE = """<!doctype html><html><body><h2>Career Opportunities: Create an Account</h2>
<form id=f onsubmit="return false">
<label for=e>Email</label><input id=e type=text><label for=p>Password</label><input id=p type=password>
<label for=r>Retype password</label><input id=r type=password>
<label for=fn>First name</label><input id=fn type=text><label for=ln>Last name</label><input id=ln type=text>
<label for=c>Country/Region of Residence</label><select id=c><option>- Select -</option><option>United States</option></select>
<div><input type=checkbox id=ack disabled> <a href="#" id=opener>Read and acknowledge the data privacy statement.</a> *</div>
<button type=button id=submit>Submit</button></form>
<div id=dlg role=dialog style="display:none"><p>Data privacy statement ...</p>
<button type=button id=accept>Acknowledge</button><button type=button id=cancel>Cancel</button></div>
<script>
opener.onclick=e=>{e.preventDefault();dlg.style.display='block'};
cancel.onclick=()=>dlg.style.display='none';
accept.onclick=()=>{dlg.style.display='none';ack.disabled=false;ack.checked=true;};
submit.onclick=()=>{window.sent=ack.checked?[e.value,p.value]:null;if(!ack.checked)window.refused=true};
</script></body></html>"""


class Site:
    """A tiny employer site that records what was posted to it."""

    def __init__(self):
        self.posts = []
        self.sent_mail = 0
        site = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_a):
                pass

            def reply(self, body, code=200):
                data = body.encode()
                self.send_response(code)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                path = urlsplit(self.path).path
                self.reply({"/guest": GUEST_PAGE, "/login": LOGIN_PAGE, "/create": CREATE_PAGE, "/gigya": GIGYA_PAGE}.get(path, "<html><body>other</body></html>"))

            def do_POST(self):
                size = int(self.headers.get("Content-Length") or 0)
                form = parse_qs(self.rfile.read(size).decode())
                path = urlsplit(self.path).path
                site.posts.append((path, {k: v[0] for k, v in form.items()}))
                if path == "/send":
                    site.sent_mail += 1
                    return self.reply(GUEST_PAGE)
                if path == "/create":
                    return self.reply(DONE_PAGE)
                self.reply(LOGIN_PAGE)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def site():
    value = Site()
    yield value
    value.close()


class FakeInbox:
    """The one thing that cannot be real: a mailbox. It hands back the link the employer's email would carry."""

    configured = True

    def __init__(self, link=""):
        self.link, self.armed, self.waits = link, [], 0

    async def rule(self, *_a):
        return True

    def live(self, challenge):
        return bool(challenge)

    async def arm(self, run_id, job, email, kind, **_k):
        self.armed.append(kind)
        return {"id": "c1", "since": 0}

    async def wait(self, challenge, max_wait_seconds=0):
        self.waits += 1
        return {"kind": "link", "value": self.link} if self.link else None

    def finish(self, *_a):
        pass

    def reason(self, *_a):
        return ""


async def build(page, service, tmp_path, url, inbox, job_url=None, ats="custom"):
    resume = tmp_path / "r.pdf"
    resume.write_bytes(b"%PDF")
    profile = Profile(first_name="Alex", last_name="Rivera", email="alex@example.test", allow_account_creation=True,
                      accept_all_application_terms=True)
    form = FormSession(page, Resolver(profile, service.db, service.config), resume, lambda *a, **k: None)
    events = []

    async def nothing(*_a, **_k):
        return None

    async def false(*_a, **_k):
        return False

    engine = SimpleNamespace(
        service=SimpleNamespace(db=service.db, config=service.config, inbox=inbox,
                                mail=SimpleNamespace(narrow=lambda *a: None, rule_for=lambda *_: True)),
        config=service.config, form=form, page=page, profile=profile, run_id="run-1",
        job={"id": "j1", "url": job_url or url, "ats": ats}, run_record={"packet": {}}, result=None,
        emit=lambda kind, message, data=None: events.append((kind, message)),
        redact=lambda u: u, extend=lambda *_: None, trace=nothing, refresh_expired_entry=false,
        email_verification=SimpleNamespace(prepare_submission=nothing), browser_challenge=nothing,
    )
    adapter = Adapter(engine)
    adapter.sign_in_page = None
    return adapter, engine, events


async def test_taleo_guest_record_email_is_requested_and_the_user_name_created(page, service, tmp_path, site):
    """Textron (Taleo) 2026-11-01: "already exists and cannot be used to create a guest submission", Send Email in plain
    sight, never pressed; the sign-in and its resets that followed never produced a mail."""
    await page.goto(site.url + "/guest")
    adapter, engine, events = await build(page, service, tmp_path, site.url + "/guest", FakeInbox(site.url + "/create"))
    adapter.max_steps = 4
    result = await adapter.drive()
    assert site.sent_mail == 1, (result, events)
    created = [form for path, form in site.posts if path == "/create"]
    assert created, (result, events, site.posts)
    assert created[0]["u"] == "alex@example.test" and created[0]["p"] and created[0]["p"] == created[0]["q"]
    record = adapter.accounts.record(site.url + "/guest")
    assert record["state"] == "password_reset" and record["guest_link_at"]


async def test_the_guest_link_is_not_requested_twice_inside_the_cooldown(page, service, tmp_path, site):
    await page.goto(site.url + "/guest")
    inbox = FakeInbox("")  # nothing arrives
    adapter, engine, events = await build(page, service, tmp_path, site.url + "/guest", inbox)
    adapter.max_steps = 3
    first = await adapter.drive()
    assert site.sent_mail == 1 and first["state"] == "waiting_browser"
    await page.goto(site.url + "/guest")
    again, _e, _ev = await build(page, service, tmp_path, site.url + "/guest", FakeInbox(""))
    again.max_steps = 3
    second = await again.drive()
    assert site.sent_mail == 1, "a second email was requested inside the cooldown"
    assert "requested recently" in second["reason"]


async def test_a_gated_privacy_checkbox_is_acknowledged_before_the_registration_is_sent(page, service, tmp_path, site):
    """Atlas Copco (SuccessFactors/SAP CDC) 2026-10-31..11-01: "Read and acknowledge the data privacy statement." with a
    disabled checkbox; Submit was pressed inert, twice, and counted as two registrations."""
    await page.goto(site.url + "/gigya")
    adapter, engine, events = await build(page, service, tmp_path, site.url + "/gigya", FakeInbox(""))
    from jobpilot.privacy import acknowledge_privacy_notice

    assert await acknowledge_privacy_notice(page, authorized=True)
    assert await page.evaluate("document.querySelector('#ack').checked")
    # And without the applicant's approval of routine terms nothing is opened.
    await page.goto(site.url + "/gigya")
    assert not await acknowledge_privacy_notice(page, authorized=False)
    assert not await page.evaluate("document.querySelector('#ack').checked")


def test_one_run_pressing_create_twice_is_one_registration_not_two():
    """Atlas Copco's record held two stamps of one run (14 s apart); the next run was told "twice in the last day"."""
    import time

    record = {"create_sent": [[time.time() - 600, "run-a"], [time.time() - 586, "run-a"]]}
    assert create_allowed(record, "run-b")
    record = {"create_sent": [[time.time() - 600, "run-a"], [time.time() - 300, "run-c"]]}
    assert not create_allowed(record, "run-b")
    stamped = create_stamp({"create_sent": [[time.time() - 60, "run-a"]]}, "run-a")
    assert len(stamped) == 1
