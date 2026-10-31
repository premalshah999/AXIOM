"""A mail window is time awake, and it is searched before it is called empty (2026-10-31).

The Mac slept 86% of the day and 20 of 63 windows ended while it slept: the window's wall-clock end passed, the next sweep
expired it before looking, and the run read "did not arrive" with the mail already in the inbox (Accenture's reset, 39 s
after its window; Farmers' matched reset expired unclaimed). Synthetic data only."""

import time

import pytest

from jobpilot.db import MailChallenge, Setting
from jobpilot.mail.polling import MATCH_HOLD_SECONDS
from support_mail import configured, message, provider


def put(service, challenge_id, **fields):
    with service.db.exclusive() as s:
        row = s.get(MailChallenge, challenge_id)
        for key, value in fields.items():
            setattr(row, key, value)


def state(service, challenge_id):
    with service.db.session() as s:
        row = s.get(MailChallenge, challenge_id)
        return row.state, row.expires, row.reason


def test_sleep_is_the_difference_between_the_wall_clock_and_the_monotonic_clock(service):
    mail = service.mail
    mail.awake_mark = (1000.0, 50.0)
    assert mail.sleep_credit(wall=1010.0, mono=60.0) == 0  # ten seconds awake
    assert mail.sleep_credit(wall=1013.0, mono=62.0) == 0  # a slow poll is not a sleep (1 s of drift)
    assert mail.sleep_credit(wall=1013.0 + 3600, mono=62.5) == pytest.approx(3599.5)  # an hour asleep
    assert mail.sleep_credit(wall=1013.0 + 3610, mono=72.5) == 0  # and awake again: counted once


async def test_a_window_that_ended_unsearched_still_gets_its_mail(service):
    """The poll was not running when the window closed; the mail arrived inside it."""
    _, run, rule = await configured(service)
    c = service.mail.begin(run["id"], rule, "alex@example.test", "code")
    now = time.time()
    put(service, c["id"], since=now - 300, expires=now - 20)
    service.mail.transport = provider([message(received=now - 100)])
    await service.mail.poll_once()
    assert state(service, c["id"])[0] == "matched"
    assert service.mail.claim(c["id"]) == {"kind": "code", "value": "483921"}


async def test_mail_that_arrived_after_the_window_never_matches_and_the_window_then_ends(service):
    _, run, rule = await configured(service)
    c = service.mail.begin(run["id"], rule, "alex@example.test", "code")
    now = time.time()
    put(service, c["id"], since=now - 300, expires=now - 20)
    service.mail.transport = provider([message(received=now - 5)])
    await service.mail.poll_once()
    assert state(service, c["id"])[0] == "expired"
    await service.mail.poll_once()
    assert state(service, c["id"])[0] == "expired"  # searched once; never reopened


async def test_a_failed_search_does_not_use_up_the_last_search(service):
    from jobpilot.mail_errors import MailTransportError

    _, run, rule = await configured(service)
    c = service.mail.begin(run["id"], rule, "alex@example.test", "code")
    now = time.time()
    put(service, c["id"], since=now - 300, expires=now - 20)

    async def dead(*args, **kwargs):
        raise MailTransportError("Gmail connection interrupted")

    real = service.mail.request
    service.mail.request = dead
    await service.mail.poll_once()
    assert state(service, c["id"])[0] == "pending"  # the socket was dead after wake: try again, do not expire
    service.mail.request = real
    service.mail.transport = provider([message(received=now - 100)])
    await service.mail.poll_once()
    assert state(service, c["id"])[0] == "matched"


async def test_a_dead_mailbox_cannot_hold_a_window_open_forever(service):
    from jobpilot.mail.polling import LAST_SEARCHES
    from jobpilot.mail_errors import MailTransportError

    _, run, rule = await configured(service)
    c = service.mail.begin(run["id"], rule, "alex@example.test", "code")
    put(service, c["id"], since=time.time() - 300, expires=time.time() - 20)

    async def dead(*args, **kwargs):
        raise MailTransportError("Gmail connection interrupted")

    service.mail.request = dead
    for _ in range(LAST_SEARCHES + 1):
        await service.mail.poll_once()
    assert state(service, c["id"])[0] == "expired"


async def test_time_asleep_is_added_to_every_open_window(service):
    _, run, rule = await configured(service)
    c = service.mail.begin(run["id"], rule, "alex@example.test", "password_reset")
    now = time.time()
    put(service, c["id"], since=now - 1500, expires=now - 300)  # closed five minutes ago, by the wall clock
    service.mail.transport = provider([message(received=now - 1000)])
    service.mail.awake_mark = (now - 1800, time.monotonic() - 60)  # 30 min of wall time, one minute awake
    await service.mail.poll_once()
    status, expires, _ = state(service, c["id"])
    assert status == "matched" or (status == "pending" and expires > now)  # 29 min of sleep were not the window's
    assert expires > now


async def test_a_matched_mail_stays_claimable_after_its_window_ends(service):
    """Farmers' reset (2026-10-26) matched 70 s before its window closed and expired unclaimed."""
    _, run, rule = await configured(service)
    c = service.mail.begin(run["id"], rule, "alex@example.test", "code")
    now = time.time()
    put(service, c["id"], expires=now + 2)  # the window closes in two seconds
    service.mail.transport = provider([message(received=now)])
    await service.mail.poll_once()
    status, expires, _ = state(service, c["id"])
    assert status == "matched" and expires >= now + MATCH_HOLD_SECONDS["code"] - 5
    with service.db.session() as s:
        assert s.get(Setting, "mail_request:" + c["id"]).value["matched_at"] >= now
    assert service.mail.claim(c["id"]) == {"kind": "code", "value": "483921"}


async def test_a_matched_mail_nobody_claims_does_not_hold_the_sender_for_ever(service):
    _, run, rule = await configured(service)
    c = service.mail.begin(run["id"], rule, "alex@example.test", "code")
    with service.db.exclusive() as s:
        s.get(MailChallenge, c["id"]).state = "matched"
        s.get(MailChallenge, c["id"]).expires = time.time() + 900
        row = s.get(Setting, "mail_request:" + c["id"])
        row.value = {**row.value, "matched_at": time.time() - 30}
    with service.db.session() as s:
        assert service.mail.sender_blocker(s, rule, "another-run") == c["id"]  # the owner is claiming it
    with service.db.exclusive() as s:
        row = s.get(Setting, "mail_request:" + c["id"])
        row.value = {**row.value, "matched_at": time.time() - 900}
    with service.db.session() as s:
        assert service.mail.sender_blocker(s, rule, "another-run") is None  # a long-held match no longer blocks others


async def test_the_wait_loop_searches_before_it_expires(service):
    _, run, rule = await configured(service)
    c = service.mail.begin(run["id"], rule, "alex@example.test", "code")
    now = time.time()
    put(service, c["id"], since=now - 300, expires=now - 1)
    service.mail.transport = provider([message(received=now - 100)])
    assert await service.mail.wait(c["id"], max_wait_seconds=5) == {"kind": "code", "value": "483921"}
