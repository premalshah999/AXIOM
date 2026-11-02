"""A code the browser was handed and never used (2026-11-01).

claim() emptied the request's payload the moment it handed the code over, and the message was already consumed, so a browser
that closed, an app that quit or a run that was replaced between the handoff and the typing lost the code for good: no search
could find the mail again and the next attempt asked the employer for another. The sealed mail now stays until the request ends;
an interruption keeps it for the same application's next attempt, once, while its hold lasts. Synthetic data only."""

import time

import pytest

from jobpilot.db import MailChallenge, Run, now
from support_mail import configured, message, provider

ME = "alex@example.test"
CODE = {"kind": "code", "value": "483921"}


def second_run(service, run_id="c" * 32, job_id="b" * 32, state="running"):
    with service.db.exclusive() as s:
        s.add(Run(id=run_id, job_id=job_id, state=state, started_at=now(), packet={"profile": {"email": ME}}))
    return run_id


def row(service, challenge_id):
    with service.db.session() as s:
        c = s.get(MailChallenge, challenge_id)
        return c.state, bool(c.payload), c.run_id


async def handed_over(service):
    box, run, rule = await configured(service)
    challenge = service.mail.begin(run["id"], rule, ME, "code")
    service.mail.transport = provider([message(received=time.time())])
    await service.mail.poll_once()
    assert service.mail.claim(challenge["id"]) == CODE
    assert row(service, challenge["id"])[:2] == ("consumed", True)  # handed over, still sealed
    return rule, run, challenge


async def test_an_interrupted_attempt_keeps_the_code_for_the_next_attempt_of_the_same_job(service):
    rule, run, challenge = await handed_over(service)
    service.mail.outcome(challenge["id"], "cancelled", "Browser attempt interrupted")
    assert row(service, challenge["id"])[:2] == ("cancelled", True)
    other = second_run(service)
    again = service.mail.begin(other, rule, ME, "code")
    assert again["id"] == challenge["id"] and row(service, again["id"]) == ("matched", True, other)
    assert service.mail.claim(again["id"]) == CODE  # no second email was asked for


async def test_the_kept_code_is_offered_once(service):
    rule, run, challenge = await handed_over(service)
    service.mail.outcome(challenge["id"], "cancelled", "Browser attempt interrupted")
    other = second_run(service)
    again = service.mail.begin(other, rule, ME, "code")
    service.mail.claim(again["id"])
    service.mail.outcome(again["id"], "cancelled", "Browser attempt interrupted")
    third = second_run(service, run_id="d" * 32)
    fresh = service.mail.begin(third, rule, ME, "code")
    assert fresh["id"] != challenge["id"]  # the site may have rejected it; the next attempt asks for its own


async def test_a_code_is_never_offered_to_another_job(service):
    rule, run, challenge = await handed_over(service)
    service.mail.outcome(challenge["id"], "cancelled", "Browser attempt interrupted")
    other = second_run(service, job_id="e" * 32)
    assert service.mail.begin(other, rule, ME, "code")["id"] != challenge["id"]


@pytest.mark.parametrize("end", ["verified", "failed"])
async def test_a_code_the_site_accepted_or_refused_is_not_kept(service, end):
    rule, run, challenge = await handed_over(service)
    service.mail.outcome(challenge["id"], end, "done")
    assert row(service, challenge["id"])[:2] == (end, False)
    other = second_run(service)
    assert service.mail.begin(other, rule, ME, "code")["id"] != challenge["id"]


async def test_a_code_whose_application_already_went_through_is_not_offered(service):
    rule, run, challenge = await handed_over(service)
    service.mail.outcome(challenge["id"], "cancelled", "No email was needed")
    with service.db.exclusive() as s:
        s.get(Run, run["id"]).state = "confirmed"
    other = second_run(service)
    assert service.mail.begin(other, rule, ME, "code")["id"] != challenge["id"]


async def test_an_application_that_stopped_after_the_handoff_keeps_its_code_until_the_hold_ends(service):
    rule, run, challenge = await handed_over(service)
    with service.db.exclusive() as s:
        s.get(Run, run["id"]).state = "failed"  # the run is over; nothing called outcome
    await service.mail.poll_once()
    assert row(service, challenge["id"])[:2] == ("cancelled", True)
    with service.db.exclusive() as s:
        s.get(MailChallenge, challenge["id"]).expires = time.time() - 1  # the hold ended
    await service.mail.poll_once()
    assert row(service, challenge["id"])[:2] == ("cancelled", False)
    other = second_run(service)
    assert service.mail.begin(other, rule, ME, "code")["id"] != challenge["id"]


async def test_a_kept_code_is_not_in_the_mail_summary(service):
    rule, run, challenge = await handed_over(service)
    service.mail.outcome(challenge["id"], "cancelled", "Browser attempt interrupted")
    shown = service.mail.summary()["challenges"]
    assert shown and all("payload" not in c for c in shown)
