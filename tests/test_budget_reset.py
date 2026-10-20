"""Owner-approved fresh budget (2026-10-17): eight Workday jobs spent their 8-minute wall-clock budget while Workday
itself was down for maintenance (06:00-17:00 UTC). An `application_budget_reset:<job>` setting ignores starts before
its time; nothing in the run history is rewritten."""

from datetime import UTC, datetime, timedelta

import pytest

from jobpilot.admission import QueueBlocked
from jobpilot.application_budget import require_allowance
from jobpilot.db import Job, Run


def seed(service):
    old = (datetime.now(UTC) - timedelta(hours=18)).isoformat()
    with service.db.exclusive() as s:
        job = Job(identity="br-1", url="https://acme.wd5.myworkdayjobs.com/en-US/Careers/job/X_R1", company="Acme",
                  title="Software Engineer", ats="workday")
        s.add(job)
        s.flush()
        for _ in range(3):
            s.add(Run(job_id=job.id, state="waiting_browser", mode="submit", packet={}, started_at=old, created_at=old))
        return job.id


def test_an_exhausted_job_stays_blocked_without_a_reset(service):
    job_id = seed(service)
    with service.db.session() as s, pytest.raises(QueueBlocked, match="eight-minute"):
        require_allowance(s, job_id)


def test_a_reset_gives_a_fresh_deadline_and_retry_count(service):
    job_id = seed(service)
    service.db.set_setting(
        "application_budget_reset:" + job_id,
        {"at": (datetime.now(UTC) - timedelta(minutes=1)).isoformat(), "reason": "Workday maintenance (owner)"},
    )
    with service.db.session() as s:
        deadline, remaining = require_allowance(s, job_id)
    assert remaining > 470
