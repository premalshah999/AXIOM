"""What an attempt that stops after the handoff keeps of the mail it was handed (2026-11-01).

A reset is the one mail the next attempt cannot ask for again: the cooldown holds off another request for hours, and the message is
consumed, so no search finds it. Its link or temporary password stays sealed for the same application's next attempt, once, while its
hold lasts. A code or a verification link is gone at the handoff: the next attempt gets a fresh one at the same click, and serving the
old one there would hide the new mail and type a dead code. Synthetic data only."""

import time

import pytest

from jobpilot.db import MailChallenge, Run, now
from support_mail import configured, message, provider

ME = "alex@example.test"
RESET = "https://careers.example.test/password/reset?t=abc123def456"
VERIFY = "https://careers.example.test/verify?t=abc123def456"
MAIL = {
    "password_reset": f"To reset your password open {RESET}",
    "link": f"Please verify your email: {VERIFY}",
    "code": "Your verification code is 483921",
}
TOKEN = {
    "password_reset": {"kind": "link", "value": RESET},
    "link": {"kind": "link", "value": VERIFY},
    "code": {"kind": "code", "value": "483921"},
}


def second_run(service, run_id="c" * 32, job_id="b" * 32, state="running"):
    with service.db.exclusive() as s:
        s.add(Run(id=run_id, job_id=job_id, state=state, started_at=now(), packet={"profile": {"email": ME}}))
    return run_id


def row(service, challenge_id):
    with service.db.session() as s:
        c = s.get(MailChallenge, challenge_id)
        return c.state, bool(c.payload), c.run_id


async def handed_over(service, kind="password_reset"):
    box, run, rule = await configured(service)
    challenge = service.mail.begin(run["id"], rule, ME, kind)
    service.mail.transport = provider([message(MAIL[kind], received=time.time())])
    await service.mail.poll_once()
    assert service.mail.claim(challenge["id"]) == TOKEN[kind]
    return rule, run, challenge


async def interrupted(service):
    rule, run, challenge = await handed_over(service)
    assert row(service, challenge["id"])[:2] == ("consumed", True)  # handed over, still sealed
    service.mail.outcome(challenge["id"], "cancelled", "Browser attempt interrupted")
    assert row(service, challenge["id"])[:2] == ("cancelled", True)
    return rule, run, challenge


async def test_an_interrupted_reset_keeps_its_link_for_the_next_attempt_of_the_same_job(service):
    rule, run, challenge = await interrupted(service)
    other = second_run(service)
    kept = service.mail.reclaim(other, rule, ME)
    assert kept["id"] == challenge["id"] and row(service, kept["id"]) == ("matched", True, other)
    assert service.mail.claim(kept["id"]) == TOKEN["password_reset"]  # no second email was asked for


async def test_the_inbox_hands_it_over_with_its_rule_so_the_link_opens_on_the_employers_origin(service):
    rule, run, challenge = await interrupted(service)
    job = {"url": "https://careers.example.test/jobs/1", "ats": "custom"}
    kept = await service.inbox.kept_reset(second_run(service), job, ME)
    assert kept["id"] == challenge["id"] and kept["rule"]["employer_origin"] == "https://careers.example.test"
    assert service.inbox.live(kept) and await service.inbox.wait(kept) == TOKEN["password_reset"]
    assert await service.inbox.kept_reset(second_run(service, run_id="d" * 32), job, ME) is None


async def test_a_temporary_password_is_kept_like_a_link(service):
    box, run, rule = await configured(service)
    challenge = service.mail.begin(run["id"], rule, ME, "password_reset")
    service.mail.transport = provider([message(f"Your temporary password is Tmp-4821xy. Sign in at {RESET}", received=time.time())])
    await service.mail.poll_once()
    assert service.mail.claim(challenge["id"]) == {"kind": "password", "value": "Tmp-4821xy"}
    assert row(service, challenge["id"])[:2] == ("consumed", True)


async def test_the_kept_link_is_offered_once(service):
    rule, run, challenge = await interrupted(service)
    other = second_run(service)
    kept = service.mail.reclaim(other, rule, ME)
    service.mail.claim(kept["id"])
    service.mail.outcome(kept["id"], "cancelled", "Browser attempt interrupted")
    assert service.mail.reclaim(second_run(service, run_id="d" * 32), rule, ME) is None


async def test_a_window_opened_before_a_click_never_gets_a_kept_mail(service):
    """It opens just before the click that asks the employer: an old link served there hides the new one."""
    rule, run, challenge = await interrupted(service)
    fresh = service.mail.begin(second_run(service), rule, ME, "password_reset")
    assert fresh["id"] != challenge["id"] and row(service, fresh["id"])[:2] == ("pending", False)


async def test_a_run_with_its_own_open_window_takes_no_kept_mail(service):
    rule, run, challenge = await interrupted(service)
    other = second_run(service)
    service.mail.begin(other, rule, ME, "password_reset")
    assert service.mail.reclaim(other, rule, ME) is None


async def test_a_kept_link_never_reaches_another_job_or_address(service):
    rule, run, challenge = await interrupted(service)
    assert service.mail.reclaim(second_run(service, job_id="e" * 32), rule, ME) is None
    assert service.mail.reclaim(second_run(service, run_id="f" * 32), rule, "someone.else@example.test") is None
    assert row(service, challenge["id"])[:2] == ("cancelled", True)  # still the first application's


@pytest.mark.parametrize(
    "state,reason",
    [
        ("verified", "Employer accepted the password reset"),
        ("failed", "New password not accepted"),
        ("expired", "Deadline ended"),
        ("cancelled", "Cancelled by applicant"),
        ("cancelled", "No email was needed"),
    ],
)
async def test_a_request_that_ended_any_other_way_keeps_nothing(service, state, reason):
    rule, run, challenge = await handed_over(service)
    service.mail.outcome(challenge["id"], state, reason)
    assert row(service, challenge["id"])[:2] == (state, False)
    assert service.mail.reclaim(second_run(service), rule, ME) is None


@pytest.mark.parametrize("done", ["confirmed", "already_applied", "submission_unknown"])
async def test_a_link_whose_application_already_went_through_is_not_offered(service, done):
    rule, run, challenge = await interrupted(service)
    with service.db.exclusive() as s:
        s.get(Run, run["id"]).state = done
    assert service.mail.reclaim(second_run(service), rule, ME) is None


async def test_an_application_that_stopped_after_the_handoff_keeps_its_link_until_the_hold_ends(service):
    rule, run, challenge = await handed_over(service)
    with service.db.exclusive() as s:
        s.get(Run, run["id"]).state = "failed"  # the run is over; nothing called outcome
    await service.mail.poll_once()
    assert row(service, challenge["id"])[:2] == ("cancelled", True)
    with service.db.exclusive() as s:
        s.get(MailChallenge, challenge["id"]).expires = time.time() - 1  # the hold ended
    await service.mail.poll_once()
    assert row(service, challenge["id"])[:2] == ("cancelled", False)
    assert service.mail.reclaim(second_run(service), rule, ME) is None


async def test_the_stopped_attempts_own_deadline_does_not_end_the_hold(service):
    rule, run, challenge = await handed_over(service)
    with service.db.exclusive() as s:
        stopped = s.get(Run, run["id"])
        stopped.state, stopped.packet = "timed_out", {**stopped.packet, "application_deadline": "2020-01-01T00:00:00+00:00"}
    await service.mail.poll_once()
    assert row(service, challenge["id"])[:2] == ("cancelled", True)
    kept = service.mail.reclaim(second_run(service), rule, ME)
    assert kept and service.mail.claim(kept["id"]) == TOKEN["password_reset"]


@pytest.mark.parametrize("kind", ["code", "link"])
async def test_a_code_or_a_verification_link_is_gone_at_the_handoff(service, kind):
    rule, run, challenge = await handed_over(service, kind)
    assert row(service, challenge["id"])[:2] == ("consumed", False)
    service.mail.outcome(challenge["id"], "cancelled", "Browser attempt interrupted")
    assert row(service, challenge["id"])[:2] == ("cancelled", False)
    other = second_run(service)
    assert service.mail.reclaim(other, rule, ME) is None
    assert service.mail.begin(other, rule, ME, kind)["id"] != challenge["id"]


async def test_a_reset_mailed_as_a_code_is_gone_at_the_handoff_too(service):
    """Its page cannot be reopened, so the code means nothing to the next attempt."""
    box, run, rule = await configured(service)
    challenge = service.mail.begin(run["id"], rule, ME, "password_reset")
    service.mail.transport = provider([message(MAIL["code"], received=time.time())])
    await service.mail.poll_once()
    assert service.mail.claim(challenge["id"]) == TOKEN["code"]
    assert row(service, challenge["id"])[:2] == ("consumed", False)


async def test_a_kept_link_is_not_in_the_mail_summary(service):
    rule, run, challenge = await interrupted(service)
    shown = service.mail.summary()["challenges"]
    assert shown and all("payload" not in c for c in shown)
    assert RESET not in str(shown)
