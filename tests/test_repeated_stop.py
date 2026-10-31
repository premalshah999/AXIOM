"""The same named stop, again and again (2026-10-24..31): after five of them a job is retried once a day, not on every
new build. The engine's catch-all stop and runs that sent nothing never count, and the owner's Apply starts it afresh."""

from datetime import UTC, datetime, timedelta

import pytest

from jobpilot.admission import QueueBlocked
from jobpilot.application_budget import REPEAT_STOP_LIMIT, repeat_stop_block, require_allowance
from jobpilot.db import Job, Run

NAMED = "Recovery stopped: Required field incomplete: Pronouns; Value not accepted: Pronouns"
OTHER = "The website did not accept known answers: State"
CATCH_ALL = "The browser did not advance within its recovery budget; the draft checkpoint is saved."
OUTAGE = "The selected model provider is unavailable; draft retained without further changes"


def seed(service, history, last_age=timedelta(hours=1)):
    """A new posting with runs oldest first as (state, reason); the newest finished `last_age` ago, 10 minutes apart."""
    now = datetime.now(UTC)
    with service.db.exclusive() as s:
        n = s.query(Job).count()
        job = Job(identity=f"rs-{n}", url=f"https://acme{n}.wd5.myworkdayjobs.com/en-US/Careers/job/X_R{n}",
                  company=f"Acme{n}", title="Software Engineer", ats="workday")
        s.add(job)
        s.flush()
        for i, (state, reason) in enumerate(history):
            at = (now - last_age - timedelta(minutes=10 * (len(history) - 1 - i))).isoformat()
            s.add(Run(job_id=job.id, state=state, mode="submit", reason=reason, packet={"engine_version": f"b{i}"},
                      created_at=at, finished_at=at))
        return job.id


def blocked(service, job_id):
    with service.db.session() as s:
        return repeat_stop_block(s, job_id)


def test_a_stop_repeated_across_builds_waits_a_day(service):
    job_id = seed(service, [("waiting_browser", NAMED)] * REPEAT_STOP_LIMIT)
    with service.db.session() as s, pytest.raises(QueueBlocked) as err:
        require_allowance(s, job_id)
    assert err.value.code == "repeated_stop" and err.value.outcome == "deferred" and err.value.not_before
    assert "5 times" in str(err.value) and "Pronouns" in str(err.value)
    assert blocked(service, seed(service, [("waiting_browser", NAMED)] * (REPEAT_STOP_LIMIT - 1))) is None


def test_it_is_retried_once_a_day_after_that(service):
    job_id = seed(service, [("waiting_browser", NAMED)] * 6, last_age=timedelta(hours=25))
    assert blocked(service, job_id) is None


def test_the_count_is_not_only_back_to_back(service):
    # ADP and Allen Institute alternated between two or three blockers; the newest stop still repeated.
    history = [("waiting_browser", NAMED), ("waiting_browser", OTHER)] * 2 + [("waiting_browser", NAMED)] * 3
    assert blocked(service, seed(service, history)).code == "repeated_stop"


def test_the_catch_all_stop_and_provider_outages_never_block(service):
    assert blocked(service, seed(service, [("waiting_browser", CATCH_ALL)] * 12)) is None
    # Outages send nothing: they neither count nor end the count (a model outage must stay retryable)...
    assert blocked(service, seed(service, [("waiting_browser", OUTAGE)] * 12)) is None
    mixed = [("waiting_browser", NAMED), ("waiting_browser", OUTAGE)] * 4
    assert blocked(service, seed(service, mixed)) is None  # four named stops are still under the limit
    assert blocked(service, seed(service, mixed + [("waiting_browser", NAMED)])).code == "repeated_stop"


def test_an_answer_the_owner_gave_ends_the_count(service):
    history = [("waiting_browser", NAMED)] * 4 + [("waiting_answer", "Needs the applicant's answer: Pronouns")]
    assert blocked(service, seed(service, history + [("waiting_browser", NAMED)])) is None


def test_the_owners_apply_starts_it_afresh(service):
    job_id = seed(service, [("waiting_browser", NAMED)] * 6)
    assert blocked(service, job_id).code == "repeated_stop"
    service.db.set_setting("application_budget_reset:" + job_id, {"at": datetime.now(UTC).isoformat(), "reason": "Owner clicked Apply"})
    assert blocked(service, job_id) is None
    with service.db.session() as s:
        require_allowance(s, job_id)


def test_a_refused_start_is_not_a_new_stop(service):
    # The start gate records its own refusal as a run; it must not end the count and invite another try.
    refusal = "The same stop has now ended this application 5 times (Recovery stopped). It is retried once a day"
    history = [("waiting_browser", NAMED)] * 5 + [("timed_out", refusal)]
    assert blocked(service, seed(service, history)).code == "repeated_stop"
