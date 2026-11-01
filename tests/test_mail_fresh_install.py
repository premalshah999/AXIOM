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
