"""A parked, unmatched code wait stops serializing the shared sender after STALE_CODE_HOLD.

UCSF (2026-10-13): two 20-minute code windows never received mail and 9 other Oracle runs
ended SENDER_BUSY behind them.
"""

import pytest

from jobpilot.db import MailChallenge, Run
from jobpilot.mail import STALE_CODE_HOLD
from support_mail import configured


def second_run(service, first):
    with service.db.session() as s:
        other = Run(job_id=first["job_id"], state="running", mode="submit", packet={})
        s.add(other)
        s.flush()
        return other.id


def age_and_park(service, request_id, holder_id, *, park):
    with service.db.exclusive() as s:
        challenge = s.get(MailChallenge, request_id)
        challenge.since -= STALE_CODE_HOLD + 60
        s.get(Run, holder_id).state = "waiting_browser" if park else "running"


async def test_stale_parked_code_wait_releases_the_sender(service):
    _, run, rule = await configured(service)
    first = service.mail.begin(run["id"], rule, "alex@example.test", "code")
    other = second_run(service, run)
    with pytest.raises(ValueError, match="Another application is verifying"):
        service.mail.begin(other, rule, "alex@example.test", "code")
    # Old but its run is still active: it keeps the sender.
    age_and_park(service, first["id"], run["id"], park=False)
    with pytest.raises(ValueError, match="Another application is verifying"):
        service.mail.begin(other, rule, "alex@example.test", "code")
    # Old and its run has stopped: the next run may verify.
    age_and_park(service, first["id"], run["id"], park=True)
    assert service.mail.begin(other, rule, "alex@example.test", "code")["id"] != first["id"]


async def test_greenhouse_code_waits_for_different_employers_run_in_parallel(service):
    from jobpilot.mail import GREENHOUSE_SENDERS
    from support_mail import RuleInput

    box, run, _ = await configured(service)
    senders = sorted(GREENHOUSE_SENDERS)

    async def rule(origin):
        return await service.mail.save_rule(
            RuleInput(mailbox_id=box["id"], employer_origin=origin, sender_domains=senders)
        )

    acme, globex = (
        await rule("https://job-boards.greenhouse.io/acme"),
        await rule("https://job-boards.greenhouse.io/globex"),
    )
    service.mail.begin(run["id"], acme, "alex@example.test", "auto")
    from jobpilot.db import Job

    with service.db.session() as s:
        globex_job = Job(
            identity="globex-1", url="https://job-boards.greenhouse.io/globex/jobs/1", company="Globex"
        )
        s.add(globex_job)
        s.flush()
        other_run = Run(job_id=globex_job.id, state="running", mode="submit", packet={})
        s.add(other_run)
        s.flush()
        other = other_run.id
    # A different employer: its code is identified by the employer name, so no serialization.
    assert service.mail.begin(other, globex, "alex@example.test", "auto")
    # The same employer twice still waits its turn.
    with service.db.session() as s:
        third_run = Run(job_id=globex_job.id, state="running", mode="submit", packet={})
        s.add(third_run)
        s.flush()
        third = third_run.id
    with pytest.raises(ValueError, match="Another application is verifying"):
        service.mail.begin(third, globex, "alex@example.test", "auto")


async def test_eightfold_tenants_verify_in_parallel(service):
    """Starbucks' code wait must not lock Qualcomm out: each tenant mails from its own domain."""
    from support_mail import RuleInput
    from jobpilot.db import Job

    box, run, _ = await configured(service)

    async def rule(origin):
        return await service.mail.save_rule(
            RuleInput(mailbox_id=box["id"], employer_origin=origin, sender_domains=["eightfold.ai"])
        )

    starbucks, qualcomm = (
        await rule("https://starbucks.eightfold.ai"),
        await rule("https://qualcomm.eightfold.ai"),
    )
    service.mail.begin(run["id"], starbucks, "alex@example.test", "code")
    with service.db.session() as s:
        job = Job(identity="qc-1", url="https://qualcomm.eightfold.ai/careers/job/1", company="Qualcomm")
        s.add(job)
        s.flush()
        other = Run(job_id=job.id, state="running", mode="submit", packet={})
        s.add(other)
        s.flush()
        other_id = other.id
    assert service.mail.begin(other_id, qualcomm, "alex@example.test", "code")["id"]
