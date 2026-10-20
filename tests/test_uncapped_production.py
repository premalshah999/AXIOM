"""Owner 2026-10-18: production runs without the eight-minute lifetime cap (it was for monitored rollouts).

The `application_budget` setting ({"max_seconds": null}) removes the wall-clock deadline: an old first start no longer
blocks the job, runs carry no `application_deadline` (their own runner budget ends them), and the three-start limit
still stops retry storms. Without the setting the 480 s cap is unchanged.
"""

from datetime import UTC, datetime, timedelta

import pytest

from jobpilot.admission import QueueBlocked
from jobpilot.application_budget import allowance, max_seconds, require_allowance
from jobpilot.db import Job, Run


def seed(service, starts):
    old = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
    with service.db.exclusive() as s:
        job = Job(identity="uc-%d" % starts, url="https://jobs.ashbyhq.com/acme/%d" % starts, company="Acme",
                  title="Software Engineer", ats="ashby")
        s.add(job)
        s.flush()
        for _ in range(starts):
            s.add(Run(job_id=job.id, state="timed_out", mode="submit", packet={}, started_at=old, created_at=old))
        return job.id


def test_default_keeps_the_eight_minute_cap(service):
    job_id = seed(service, 1)
    with service.db.session() as s:
        assert max_seconds(s) == 480
        with pytest.raises(QueueBlocked, match="eight-minute"):
            require_allowance(s, job_id)


def test_uncapped_production_has_no_lifetime_deadline(service):
    service.db.set_setting("application_budget", {"max_seconds": None, "reason": "owner"})
    job_id = seed(service, 1)
    with service.db.session() as s:
        assert max_seconds(s) is None
        deadline, remaining, starts = allowance(s, job_id)
        assert deadline is None and remaining is None and starts == 1
        assert require_allowance(s, job_id) == (None, None)


def test_uncapped_production_still_limits_starts(service):
    service.db.set_setting("application_budget", {"max_seconds": None})
    job_id = seed(service, 3)
    with service.db.session() as s, pytest.raises(QueueBlocked, match="two retries"):
        require_allowance(s, job_id)
