"""The employer-mail wait end to end: a local fake Gmail IMAP server, jobpilot/gmail_imap.py over a real socket, the mail
service's poll, and a local form in real Chromium (2026-11-01).

Last week's stops on employer mail (7 days to 2026-11-01): 39 "another application kept the same email sender busy"
(each a browser opened for nothing), 74 "replaced by a newer attempt" (56 of them matched mail that the next run used) and
~190 windows that ended with no candidate mail at all. A search that fails looks like an empty mailbox (the accent in
"postulación" did it for five days), so these run the real search, fetch and match against a server that really holds,
or does not hold, the mail."""

import asyncio
import imaplib
import json
import threading
import time

import pytest
from sqlalchemy import select

import fake_imap
from jobpilot import gmail_imap
from jobpilot.db import Job, Mailbox, MailChallenge, Run, now
from jobpilot.mail_errors import MailPending, MailSenderBusy
from support_budget import stretch_playwright_ceilings
from support_mail import seed  # noqa: F401  (configured() is used through setup_engine)
from test_mail_browser import setup_engine

CODE_PAGE = (
    '<h1>Enter verification code</h1><form method="post" action="/verify-code">'
    '<label>Verification code<input autocomplete="one-time-code" name="code"></label><button>Verify email</button></form>'
)
SENDER = "Example Careers <no-reply@notify.example.test>"
ME = "alex@example.test"


@pytest.fixture
def imap(monkeypatch):
    server, box = fake_imap.start()
    port = server.server_address[1]
    monkeypatch.setattr(imaplib, "IMAP4_SSL", lambda host, p, **kw: imaplib.IMAP4("127.0.0.1", port, timeout=kw.get("timeout")))
    # How long a run waits in the browser for a code that comes: paid only by one that never does, and the test of that sets its own.
    # The app's 30 s ran out on a starved Mac with the mail already in the box.
    monkeypatch.setattr("jobpilot.email_browser.code_inline_wait", lambda ats: 300)
    stretch_playwright_ceilings(monkeypatch)  # a click on Verify bounded at 5 s ran out with the page already moving on
    for cache in (gmail_imap._clients, gmail_imap._where, gmail_imap._identity):
        cache.clear()
    yield box
    server.shutdown()
    server.server_close()
    gmail_imap._clients.clear()


def use_imap(service):
    with service.db.exclusive() as s:
        s.scalars(select(Mailbox)).first().credentials = service.mail.vault.seal(
            {"provider": "gmail_imap", "address": ME, "app_password": "abcdabcdabcdabcd"}
        )


async def sweep_from_now(mail):
    """One mailbox sweep that starts after this call, so it reads what the test just wrote. poll_once returns at once while
    another sweep holds the lock (on purpose: a browser owner must not line up behind it), and the poller task below is in one
    nearly all the time: on a loaded Mac two poll_once calls in a row both returned without reading anything and the window
    still read "pending" (2026-11-01). Wait out the sweep in flight, then start one: nothing else can run between the release
    and poll_once's look at the lock, so this is never skipped, and no sleep is involved."""
    async with mail.poll_lock:
        pass
    await mail.poll_once()


def later(seconds, fn, *args, **kwargs):
    timer = threading.Timer(seconds, lambda: fn(*args, **kwargs))
    timer.start()
    return timer


def code_mail(box, code="483921", to=ME):
    box.deliver(sender=SENDER, to=to, subject="Your verification code", body=f"Your verification code is {code}")


async def run_form(service, page, tmp_path, resume_pdf, imap):
    e, seen = await setup_engine(service, page, tmp_path, resume_pdf, CODE_PAGE)
    use_imap(service)
    return e, seen


async def test_a_code_that_arrives_late_is_entered_and_the_application_form_opens(service, page, tmp_path, resume_pdf, imap):
    e, seen = await run_form(service, page, tmp_path, resume_pdf, imap)
    timer = later(2.0, code_mail, imap)  # the employer's mail is slower than the first polls
    poller = asyncio.create_task(service.mail.poll())
    try:
        assert await e.email_verification.handle()
        assert await page.get_by_label("Full name").is_visible()
        assert [c["state"] for c in service.mail.summary()["challenges"]] == ["verified"]
        assert sum("/verify-code" in url for url, method in seen if method == "POST") == 1
        assert imap.searches and "483921" not in json.dumps(service.mail.summary())
    finally:
        timer.cancel()
        poller.cancel()
        await asyncio.gather(poller, return_exceptions=True)
        e.email_verification.close()


async def test_a_code_mailed_for_another_run_on_the_same_sender_is_never_typed(service, page, tmp_path, resume_pdf, imap):
    e, seen = await run_form(service, page, tmp_path, resume_pdf, imap)
    # Another application's code, from the same sender, to its own plus-address, lands first; ours follows.
    timers = [later(1.0, code_mail, imap, "111111", "alex+other@example.test"), later(3.0, code_mail, imap)]
    poller = asyncio.create_task(service.mail.poll())
    try:
        assert await e.email_verification.handle()
        assert sum("/verify-code" in url for url, method in seen if method == "POST") == 1  # one try, the right code
        assert await page.get_by_label("Full name").is_visible()
    finally:
        for timer in timers:
            timer.cancel()
        poller.cancel()
        await asyncio.gather(poller, return_exceptions=True)
        e.email_verification.close()


async def test_a_code_that_never_comes_parks_the_run_and_the_window_ends_empty_not_failed(
    service, page, tmp_path, resume_pdf, imap, monkeypatch
):
    monkeypatch.setattr("jobpilot.email_browser.code_inline_wait", lambda ats: 3)
    e, seen = await run_form(service, page, tmp_path, resume_pdf, imap)
    poller = asyncio.create_task(service.mail.poll())
    try:
        with pytest.raises(MailPending) as parked:
            await e.email_verification.handle()
        challenge_id = parked.value.challenge["challenge_id"]
        assert not any("/verify-code" in url for url, _ in seen)  # nothing was typed
        # The durable window runs out with the mailbox empty: it ends as an empty search, with the searches on record.
        with service.db.exclusive() as s:
            row = s.get(MailChallenge, challenge_id)
            row.expires = time.time() - 1
        await sweep_from_now(service.mail)
        with service.db.session() as s:
            row = s.get(MailChallenge, challenge_id)
            assert row.state == "expired" and "deadline ended" in row.reason
        assert imap.searches  # the mailbox was really searched; the window did not end on a failed search
        assert service.db.get_setting("mail_failures", {}).get("poll_scan") is None
    finally:
        poller.cancel()
        await asyncio.gather(poller, return_exceptions=True)
        e.email_verification.close()


async def test_two_runs_at_one_sender_the_second_waits_in_the_queue_then_gets_its_own_code(
    service, page, tmp_path, resume_pdf, imap, monkeypatch
):
    monkeypatch.setattr("jobpilot.inbox.LEASE_WAIT_CAP_SECONDS", 0)
    e, seen = await setup_engine(service, page, tmp_path, resume_pdf, CODE_PAGE, arm_request=False)
    use_imap(service)
    rule = service.mail.rule_for(e.job["url"])
    first_run, first_job = "c" * 32, "d" * 32
    with service.db.exclusive() as s:
        s.add(Job(id=first_job, identity="first", url="https://careers.example.test/job/1", ats="custom"))
        s.add(Job(id="b" * 32, identity="second", url="https://careers.example.test/job/2", ats="custom"))
        s.add(Run(id=first_run, job_id=first_job, state="running", started_at=now(), packet={"profile": {"email": ME}}))
    held = service.mail.begin(first_run, rule, ME, "code")
    # 1. The second job is not started while the first holds the sender: no browser opens, no run is created.
    with service.db.session() as s:
        assert not service.mail.shared_sender_free(s, s.get(Job, "b" * 32))
        assert service.mail.shared_sender_free(s, s.get(Job, first_job))
        before = len(list(s.scalars(select(Run.id))))
    # 2. Had it started anyway (the lease was taken after admission), the browser stops at once without a request.
    with pytest.raises(MailSenderBusy):
        await e.email_verification.acquire("code")
    assert not any("/verify-code" in url for url, _ in seen)
    # 3. The first run's code arrives and goes to the first run only.
    timer = later(1.0, code_mail, imap, "222222")
    poller = asyncio.create_task(service.mail.poll())
    try:
        token = await service.mail.wait(held["id"], max_wait_seconds=60)  # returns when the code matches
        assert token["value"] == "222222"
        service.mail.outcome(held["id"], "verified", "Website accepted the verification code")
        with service.db.session() as s:
            assert service.mail.shared_sender_free(s, s.get(Job, "b" * 32))  # the queue lets it start now
            assert len(list(s.scalars(select(Run.id)))) == before
        # 4. The second run, started after the first released, asks for its own code and gets only that one.
        with service.db.exclusive() as s:
            s.get(Run, e.run_id).started_at = now()
        timer2 = later(2.0, code_mail, imap)
        assert await e.email_verification.handle()
        assert await page.get_by_label("Full name").is_visible()
        assert sum("/verify-code" in url for url, method in seen if method == "POST") == 1
        timer2.cancel()
    finally:
        timer.cancel()
        poller.cancel()
        await asyncio.gather(poller, return_exceptions=True)
        e.email_verification.close()
