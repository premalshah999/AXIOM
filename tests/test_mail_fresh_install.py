"""A new install's mail, from an empty database: every platform's real-format code, link and reset (2026-11-01).

Friends who installed the app saw employer mail reach their Gmail and the app not use it, on every platform. Nothing here
is seeded: the mailbox is connected through the route onboarding calls, a real socket carries IMAP (tests/fake_imap.py),
and each window is opened the way an adapter opens one."""

import imaplib
import json
import os
import time

import pytest
from sqlalchemy import select

import fake_imap
from jobpilot import gmail_imap
from jobpilot.db import Job, MailChallenge, Mailbox, MailRule, Run, now, record
from jobpilot.mail.helpers import origin
from mail_formats import KINDS, ME, PLATFORMS, deliver, fresh_code

ALL = [(p, k) for p in PLATFORMS for k in KINDS]
WINDOW = {"code": "code", "link": "link", "reset": "password_reset"}


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


def onboard(client, email=ME):
    """What the Connections step sends."""
    r = client.post("/api/mail/imap", json={"email": email, "app_password": "abcd efgh ijkl mnop"})
    assert r.status_code == 200, r.text


def start_run(service, url, ats, company, email=ME):
    with service.db.exclusive() as s:
        job = Job(identity=url, url=url, ats=ats, company=company, title="Analyst", demo=False, status="running")
        s.add(job)
        s.flush()
        run = Run(job_id=job.id, state="running", started_at=now(), packet={"profile": {"email": email}})
        s.add(run)
        s.flush()
        return record(job), record(run)


def link_for(spec, kind):
    """Where the platform's button points: its tracker on iCIMS, the employer's own host otherwise."""
    token = "tok" + os.urandom(6).hex()
    if spec["ats"] == "icims":
        return f"https://{'tracking' if kind == 'link' else 'clicks'}.icims.com/c/{token}"
    path = "/verify?token=" if kind == "link" else "/password/reset?token="
    return origin(spec["url"]) + path + token


def note(platform, kind, outcome, service, challenge):
    path = os.environ.get("MAILFRESH_REPORT")
    if not path:
        return
    with open(path, "a") as f:
        f.write(json.dumps({"platform": platform, "kind": kind, "outcome": outcome, "reason": service.inbox.reason(challenge) if challenge else ""}) + "\n")


@pytest.mark.parametrize("platform,kind", ALL)
async def test_an_empty_database_uses_each_platforms_mail(client, service, imap, platform, kind):
    service.config.mail_poll_seconds = 0.1
    spec = PLATFORMS[platform]
    with service.db.session() as s:
        assert not list(s.scalars(select(MailRule)))  # nothing learned
    onboard(client)
    job, run = start_run(service, spec["url"], spec["ats"], spec["company"])
    challenge = await service.inbox.arm(run["id"], job, ME, WINDOW[kind])
    if not challenge:
        note(platform, kind, "no window: no sender rule", service, None)
    assert challenge, "no window opened: this employer has no sender rule"
    time.sleep(1.1)  # Gmail's date has whole seconds; the mail is not older than the request.
    carried = deliver(imap, platform, kind, code=fresh_code() if kind == "code" else None, link=None if kind == "code" else link_for(spec, kind))
    try:
        token = await service.inbox.wait(challenge, max_wait_seconds=4)
    except Exception as exc:
        note(platform, kind, type(exc).__name__, service, challenge)
        raise AssertionError(f"not matched: {service.inbox.reason(challenge)}") from None
    note(platform, kind, "ok" if token and token["value"] == carried else "wrong", service, challenge)
    assert token and token["value"] == carried


async def parked_run(service, *, autonomous):
    """A run that asked for a link and parked: the state the mail poll finds it in, on a new install."""
    service.db.set_setting("profile", {"name": "Alex Rivera", "email": ME, "autonomous": autonomous})
    service.db.set_setting("control", {"paused": False, "auto_submit": True})
    job, run = start_run(service, PLATFORMS["custom"]["url"], "custom", PLATFORMS["custom"]["company"])
    challenge = await service.inbox.arm(run["id"], job, ME, "link")
    handle = {"challenge_id": challenge["id"], "id": challenge["id"]}
    with service.db.exclusive() as s:
        row = s.get(Run, run["id"])
        row.mode, row.state = "submit", "waiting_email"
        row.packet = {**row.packet, "match_approved": True, "waiting_kind": "email", "wait_handle": handle, "profile": {"email": ME, "autonomous": autonomous}}
        s.get(Job, job["id"]).status = "waiting_email"
        s.get(MailChallenge, challenge["id"]).state = "matched"
    return job, run, challenge


@pytest.mark.parametrize("autonomous", [False, True])
async def test_a_matched_mail_wakes_its_run_whatever_the_apply_on_its_own_switch_says(service, monkeypatch, autonomous):
    """The switch is off unless someone turns it on. With it off the mail matched, the event said so, and nothing came of it:
    the run waited until its window closed and then said the email did not arrive."""
    onboard_box(service)
    job, run, challenge = await parked_run(service, autonomous=autonomous)
    queued = []

    async def queue(job_id, mode, **kwargs):
        queued.append((job_id, mode, kwargs.get("recovery_source_run")))

    monkeypatch.setattr(service, "queue", queue)
    await service.resume_mail_wait(run["id"], challenge["id"])
    assert queued == [(job["id"], "submit", run["id"])]


def onboard_box(service):
    with service.db.exclusive() as s:
        s.add(Mailbox(email=ME, credentials=service.mail.vault.seal({"provider": "gmail_imap", "address": ME, "app_password": "abcdefghijklmnop"})))


SHARED = [p for p in PLATFORMS if p != "custom"]


@pytest.mark.parametrize("platform", SHARED)
async def test_two_employers_on_one_shared_sender_never_get_each_others_code(client, service, imap, platform):
    """Both employers' runs are open, or the second waits behind the first's lease; both codes arrive, the other's first. Each
    run gets its own code or nothing, on every platform that mails all its employers from one sender."""
    service.config.mail_poll_seconds = 0.1
    spec, other = PLATFORMS[platform], PLATFORMS[platform]["other"]
    onboard(client)
    job_a, run_a = start_run(service, spec["url"], spec["ats"], spec["company"])
    job_b, run_b = start_run(service, other["url"], spec["ats"], other["company"])
    first = await service.inbox.arm(run_a["id"], job_a, ME, "code", patience=0)
    assert first
    try:
        second = await service.inbox.arm(run_b["id"], job_b, ME, "code", patience=0)
    except ValueError as exc:
        assert "Another application" in str(exc)
        second = None  # Held behind the first employer's lease: it asks for no mail until that is over.
    time.sleep(1.1)
    code_b = deliver(imap, platform, "code", other=True) if second else None
    code_a = deliver(imap, platform, "code")
    got = {}
    for name, challenge in (("a", first), ("b", second)):
        if not challenge:
            continue
        try:
            token = await service.inbox.wait(challenge, max_wait_seconds=4)
            got[name] = token["value"] if token else None
        except Exception:
            got[name] = None
    if second:
        assert got.get("a") in {code_a, None} and got.get("b") in {code_b, None}, (platform, "took the other employer's code")
        assert got.get("a") == code_a and got.get("b") == code_b, (platform, "own code not recognised", got == {})
    else:
        assert got["a"] == code_a
        # The first finishes and releases; the second, asked only now, sees its own code and not the first's, which was consumed.
        service.inbox.finish(first, "verified", "Website accepted")
        second = await service.inbox.arm(run_b["id"], job_b, ME, "code", patience=0)
        assert second
        time.sleep(1.1)
        code_b = deliver(imap, platform, "code", other=True)
        token = await service.inbox.wait(second, max_wait_seconds=4)
        assert token and token["value"] == code_b


async def test_a_code_from_another_employer_on_the_shared_sender_is_refused_in_a_sole_window(client, service, imap):
    """A code that arrives for an employer whose run has ended, in the window of another on the same platform that mails naming
    the tenant in its sender."""
    service.config.mail_poll_seconds = 0.1
    spec = PLATFORMS["icims"]
    onboard(client)
    job, run = start_run(service, spec["url"], spec["ats"], spec["company"])
    challenge = await service.inbox.arm(run["id"], job, ME, "code", patience=0)
    time.sleep(1.1)
    stranger = deliver(imap, "icims", "code", other=True)
    with pytest.raises(Exception):
        await service.inbox.wait(challenge, max_wait_seconds=2)
    assert stranger not in json.dumps(service.mail.summary())


async def test_mail_to_an_address_that_forwards_into_the_connected_gmail_is_read(client, service, imap):
    """Someone applies as a school address and connects the Gmail it forwards to. No rule existed for them on any platform."""
    service.config.mail_poll_seconds = 0.1
    spec = PLATFORMS["greenhouse"]
    school = "alex@school.example"
    onboard(client)
    job, run = start_run(service, spec["url"], spec["ats"], spec["company"], email=school)
    challenge = await service.inbox.arm(run["id"], job, school, "code")
    assert challenge, "no window: the Gmail that receives the forwarded mail was not used"
    time.sleep(1.1)
    code = deliver(imap, "greenhouse", "code", to=school, delivered_to=ME)
    assert (await service.inbox.wait(challenge, max_wait_seconds=4))["value"] == code


async def test_mail_to_someone_else_never_matches_an_address_forwarded_here(client, service, imap):
    service.config.mail_poll_seconds = 0.1
    spec = PLATFORMS["greenhouse"]
    onboard(client)
    job, run = start_run(service, spec["url"], spec["ats"], spec["company"], email="alex@school.example")
    challenge = await service.inbox.arm(run["id"], job, "alex@school.example", "code")
    time.sleep(1.1)
    deliver(imap, "greenhouse", "code", to=ME)  # Addressed to the Gmail itself, not to this application's address.
    with pytest.raises(Exception):
        await service.inbox.wait(challenge, max_wait_seconds=2)


async def test_two_gmails_and_neither_is_this_address_says_nothing_rather_than_guessing(client, service, imap):
    onboard(client)
    onboard(client, "second.applicant@gmail.com")
    spec = PLATFORMS["greenhouse"]
    job, run = start_run(service, spec["url"], spec["ats"], spec["company"], email="alex@school.example")
    assert await service.inbox.arm(run["id"], job, "alex@school.example", "code") is None


async def test_a_mailbox_connected_in_the_app_reads_as_connected_and_checks_out(client, service, imap):
    onboard(client)
    assert client.get("/api/snapshot").json()["config"]["imap"] is True
    checked = client.post("/api/inbox/check", json={}).json()
    assert checked.get("connected") is True, checked


async def test_one_gmail_label_that_cannot_be_parsed_does_not_stop_the_search():
    class Client:
        def list(self):
            return "OK", [b'(\\HasNoChildren) "/" "INBOX"', (b'(\\HasNoChildren) "/" {3}', b'a"b'), b"", b'(\\All) "/" "[Gmail]/All Mail"', b'(\\Junk) "/" "[Gmail]/Spam"']

    assert gmail_imap._folders(Client()) == ['"INBOX"', '"[Gmail]/All Mail"', '"[Gmail]/Spam"']


def test_a_mail_in_the_second_its_window_opened_is_inside_it():
    from jobpilot.mail import extract_message
    from support_mail import message

    now_s = int(time.time())
    rule = {"sender_domains": ["notify.example.test"], "link_origins": ["https://careers.example.test"]}
    window = {"recipient": "alex@example.test", "since": now_s + 0.7, "expires": now_s + 60, "kind": "code"}
    assert extract_message(message(received=now_s), rule, window)["value"] == "483921"
    assert extract_message(message(received=now_s - 1), rule, window) is None


def test_google_saying_a_domain_without_dmarc_aligns_counts_and_nothing_else_does():
    from jobpilot.mail import authenticated_sender

    assert authenticated_sender("mx.google.com; dkim=pass; dmarc=bestguesspass header.from=small.example", "small.example")
    assert not authenticated_sender("mx.google.com; dkim=fail; dmarc=fail header.from=small.example", "small.example")
    assert not authenticated_sender("mx.google.com; dkim=pass", "small.example")


async def test_seeded_senders_are_on_a_new_rule(client, service, imap):
    onboard(client)
    url = "https://humana.wd5.myworkdayjobs.com/en-US/Careers/job/Analyst_R1"
    job, _ = start_run(service, url, "workday", "Humana")
    rule = await service.mail.ensure_rule(job, ME)
    assert "humana.com" in rule["sender_domains"] and "myworkday.com" in rule["sender_domains"]


async def test_a_page_already_showing_a_code_box_reaches_back_to_the_click_that_sent_it(client, service, imap):
    onboard(client)
    spec = PLATFORMS["lever"]
    job, run = start_run(service, spec["url"], spec["ats"], spec["company"])
    rule = await service.mail.ensure_rule(job, ME)
    with service.db.exclusive() as s:
        s.get(Run, run["id"]).started_at = "2020-01-01T00:00:00+00:00"
    begun = time.time()
    c = service.mail.begin(run["id"], rule, ME, "code", since=begun - 900)
    assert begun - 31 <= c["since"] <= begun - 29  # the click just before this page, never the whole run


async def test_a_link_window_whose_page_asks_for_a_code_accepts_the_code(client, service, imap):
    service.config.mail_poll_seconds = 0.1
    spec = PLATFORMS["workday"]
    onboard(client)
    job, run = start_run(service, spec["url"], spec["ats"], spec["company"])
    challenge = await service.inbox.arm(run["id"], job, ME, "link")
    service.mail.restrict_kind(challenge["id"], run["id"], "code")
    time.sleep(1.1)
    code = deliver(imap, "workday", "code")
    assert (await service.inbox.wait(challenge, max_wait_seconds=4))["value"] == code
