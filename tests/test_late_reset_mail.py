"""A reset mail that arrives after its window closed is matched on the next attempt, not discarded or asked for again.

Accenture's reset arrived 39 s after its 20-minute window, Fresenius' 2 min 14 s, Barclays' activation 2 min 38 s
(2026-10-29/31); each was past the window, so the next run waited out a cooldown or asked again, which voids the first
link. Synthetic data only."""

import base64
import time
from collections import Counter
from types import SimpleNamespace

import httpx

from jobpilot.adapters.base import Adapter
from jobpilot.db import MailChallenge, Run
from jobpilot.mail import RuleInput
from jobpilot.mail.challenges import LATE_MAIL_VALIDITY
from support_mail import seed

SITE = "https://careers.acme.test"
ADDRESS = "alex@example.test"


def link_mail(message_id, received, link="https://careers.acme.test/reset?t=" + "x" * 12, sender="mailer.acme.test"):
    body = f"Reset your password: {link}"
    return {
        "id": message_id,
        "internalDate": str(int(received * 1000)),
        "payload": {
            "mimeType": "text/plain",
            "headers": [
                {"name": "From", "value": f"Acme Careers <noreply@{sender}>"},
                {"name": "To", "value": f"Alex <{ADDRESS}>"},
                {"name": "Subject", "value": "Reset your password"},
                {"name": "Authentication-Results", "value": f"mx.google.com; dkim=pass; dmarc=pass header.from={sender}"},
            ],
            "body": {"data": base64.urlsafe_b64encode(body.encode()).decode().rstrip("=")},
        },
    }


def mailbox(messages):
    def route(request):
        if request.url.path.endswith("/messages"):
            return httpx.Response(200, json={"messages": [{"id": m["id"]} for m in messages]})
        return httpx.Response(200, json=next(m for m in messages if request.url.path.endswith("/" + m["id"])))

    return httpx.MockTransport(route)


async def setup(service):
    box, run = seed(service, employer=SITE)
    rule = await service.mail.save_rule(RuleInput(mailbox_id=box["id"], employer_origin=SITE, sender_domains=["acme.test"]))
    return run, rule


def earlier(service, run, rule, *, kind="password_reset", state="expired", age=1500, reason="x", message_id=""):
    """A window of this employer that ended without a mail, `age` seconds after it opened 1500 s ago."""
    with service.db.exclusive() as s:
        row = MailChallenge(run_id=run["id"], rule_id=rule["id"], mailbox_id=rule["mailbox_id"], recipient=ADDRESS,
                            kind=kind, state=state, since=time.time() - age, expires=time.time() - age + 1200,
                            reason=reason, message_id=message_id)
        s.add(row)
        s.flush()
        return row.id, row.since


async def test_a_new_reset_window_starts_at_the_unanswered_request(service):
    run, rule = await setup(service)
    _, since = earlier(service, run, rule)
    c = service.mail.begin(run["id"], rule, ADDRESS, "password_reset")
    assert c["since"] == since and c["late_window"] is True


async def test_only_an_unanswered_request_of_the_same_kind_inside_its_validity_is_inherited(service):
    run, rule = await setup(service)
    earlier(service, run, rule, kind="code")  # another kind
    earlier(service, run, rule, message_id="matched-mail")  # that one was answered
    earlier(service, run, rule, age=LATE_MAIL_VALIDITY + 600)  # too long ago for a link to be waited for
    earlier(service, run, rule, state="cancelled", reason="No email was needed")  # no request was made
    c = service.mail.begin(run["id"], rule, ADDRESS, "password_reset")
    assert c["late_window"] is False and c["since"] > time.time() - 120


async def test_a_reset_mail_that_arrived_after_the_window_closed_is_matched_by_the_next_window(service):
    run, rule = await setup(service)
    _, since = earlier(service, run, rule)  # closed 5 minutes ago
    c = service.mail.begin(run["id"], rule, ADDRESS, "password_reset")
    service.mail.transport = mailbox([link_mail("late-1", time.time() - 240)])  # arrived 4 minutes ago, after the first window
    await service.mail.poll_once()
    assert service.mail.claim(c["id"])["kind"] == "link"


async def test_the_newest_of_two_reset_links_is_the_valid_one(service):
    run, rule = await setup(service)
    earlier(service, run, rule)
    c = service.mail.begin(run["id"], rule, ADDRESS, "password_reset")
    old = link_mail("old", time.time() - 240, link="https://careers.acme.test/reset?t=" + "a" * 12)
    new = link_mail("new", time.time() - 60, link="https://careers.acme.test/reset?t=" + "b" * 12)
    service.mail.transport = mailbox([old, new])
    await service.mail.poll_once()
    assert service.mail.claim(c["id"]) == {"kind": "link", "value": "https://careers.acme.test/reset?t=" + "b" * 12}


async def test_two_reset_links_at_the_same_instant_stay_ambiguous(service):
    run, rule = await setup(service)
    c = service.mail.begin(run["id"], rule, ADDRESS, "password_reset")
    at = time.time()
    service.mail.transport = mailbox([
        link_mail("a", at, link="https://careers.acme.test/reset?t=" + "a" * 12),
        link_mail("b", at, link="https://careers.acme.test/reset?t=" + "b" * 12),
    ])
    await service.mail.poll_once()
    with service.db.session() as s:
        assert s.get(MailChallenge, c["id"]).state == "failed"


async def test_another_employers_reset_link_is_never_the_newest(service):
    """Newest wins only among links on this employer's own origin; a link elsewhere is not a candidate at all."""
    run, rule = await setup(service)
    earlier(service, run, rule)
    c = service.mail.begin(run["id"], rule, ADDRESS, "password_reset")
    ours = link_mail("ours", time.time() - 120, link="https://careers.acme.test/reset?t=" + "a" * 12)
    theirs = link_mail("theirs", time.time() - 10, link="https://careers.other.test/reset?t=" + "b" * 12)
    service.mail.transport = mailbox([ours, theirs])
    await service.mail.poll_once()
    assert service.mail.claim(c["id"]) == {"kind": "link", "value": "https://careers.acme.test/reset?t=" + "a" * 12}


def adapter(service, record, *, arrives, kept=None):
    value = Adapter.__new__(Adapter)
    value.service, value.attempts = service, Counter()
    value.challenge = None
    async def nothing_kept(*_a):
        return kept

    value.inbox = SimpleNamespace(configured=True, live=lambda challenge: False, kept_reset=nothing_kept)
    value.e, value.job, value.accounts = SimpleNamespace(run_id="r1"), {}, SimpleNamespace(email="alex@example.test")
    value.calls = []

    async def arm_mail(kind="auto"):
        value.calls.append(("arm", kind))
        value.challenge = {"id": "c1", "late_window": True}
        return True

    async def poll_once():
        value.calls.append("poll")

    async def complete(url):
        value.calls.append(("complete", url))
        return {"state": "done"}

    value.arm_mail, value.complete_password_reset = arm_mail, complete
    value.release_mail = lambda: (value.calls.append("release"), setattr(value, "challenge", None))
    value.emit = lambda *a, **k: None
    value.service = SimpleNamespace(
        mail=SimpleNamespace(poll_once=poll_once),
        db=SimpleNamespace(session=lambda: _Session("matched" if arrives else "pending")),
    )
    return value


class _Session:
    def __init__(self, state):
        self.state = state

    def __enter__(self):
        return SimpleNamespace(get=lambda model, key: SimpleNamespace(state=self.state))

    def __exit__(self, *args):
        return False


ASKED = {"reset_requested_at": time.time() - 1500, "reset_submitted_at": "2026-10-31T05:43:00+00:00"}


async def test_the_next_attempt_uses_the_late_mail_instead_of_asking_again(service):
    value = adapter(service, ASKED, arrives=True)
    assert await value.earlier_reset_mail("https://careers.acme.test/", dict(ASKED)) == {"state": "done"}
    assert value.calls == [("arm", "password_reset"), "poll", ("complete", "https://careers.acme.test/")]
    assert value.reset_in_progress is True


async def test_a_reset_mail_an_interrupted_attempt_held_is_used_without_searching_or_asking(service):
    held = {"id": "kept-1"}
    value = adapter(service, ASKED, arrives=False, kept=held)
    assert await value.earlier_reset_mail("https://careers.acme.test/", dict(ASKED)) == {"state": "done"}
    assert value.calls == [("complete", "https://careers.acme.test/")]
    assert value.challenge == held and value.reset_in_progress is True


async def test_when_nothing_arrived_the_window_is_released_and_the_usual_path_follows(service):
    value = adapter(service, ASKED, arrives=False)
    assert await value.earlier_reset_mail("u", dict(ASKED)) is None
    assert value.calls == [("arm", "password_reset"), "poll", "release"]
    assert await value.earlier_reset_mail("u", dict(ASKED)) is None and value.calls.count("poll") == 1  # once per run


async def test_nothing_is_looked_for_where_no_reset_was_confirmed_or_it_is_long_ago(service):
    for record in (
        {},
        {"reset_requested_at": time.time() - 100},  # the page never confirmed a request
        {"reset_requested_at": time.time() - LATE_MAIL_VALIDITY - 100, "reset_submitted_at": "x"},
    ):
        value = adapter(service, record, arrives=True)
        assert await value.earlier_reset_mail("u", record) is None and not value.calls
