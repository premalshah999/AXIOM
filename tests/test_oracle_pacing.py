"""Oracle concurrency (2026-10-17: 21 jobs lost to "email sender busy" when 47 runs reached the code step in one
hour). Since codes are attributed per tenant (2026-10-18), at most four Oracle applications are queued or active; a
fifth is deferred at queue time and cannot claim a browser, while other platforms are unaffected."""

from datetime import UTC, datetime

import pytest

from jobpilot.admission import QueueBlocked
from jobpilot.application_pacing import may_start, require_ats_admission
from jobpilot.db import Job, Run


def add(service, ats, state, n):
    with service.db.exclusive() as s:
        job = Job(identity=f"{ats}-{n}", url=f"https://acme{n}.fa.oraclecloud.com/job/{n}" if ats == "oracle"
                  else f"https://acme{n}.wd5.myworkdayjobs.com/job/{n}", company=f"Acme {n}", title="Engineer", ats=ats)
        s.add(job)
        s.flush()
        run = Run(job_id=job.id, state=state, mode="submit", packet={}, created_at=datetime.now(UTC).isoformat())
        s.add(run)
        s.flush()
        return job.id, run.id


def test_a_fifth_oracle_application_waits(service):
    add(service, "oracle", "running", 1)
    add(service, "oracle", "queued", 2)
    add(service, "oracle", "running", 3)
    add(service, "oracle", "queued", 4)
    job_id, run_id = add(service, "oracle", "queued", 5)
    with service.db.session() as s:
        job = s.get(Job, job_id)
        with pytest.raises(QueueBlocked) as blocked:
            require_ats_admission(s, job)
        assert blocked.value.outcome == "deferred" and blocked.value.code == "ats_pacing"


def test_only_four_oracle_runs_own_a_browser(service):
    add(service, "oracle", "running", 1)
    add(service, "oracle", "submitting", 2)
    add(service, "oracle", "running", 3)
    add(service, "oracle", "running", 4)
    job_id, run_id = add(service, "oracle", "queued", 5)
    with service.db.session() as s:
        assert not may_start(s, s.get(Job, job_id), s.get(Run, run_id))


def test_other_platforms_are_unaffected_and_the_limit_is_a_setting(service):
    for n in range(1, 6):
        add(service, "oracle", "running", n)
    job_id, run_id = add(service, "workday", "queued", 9)
    with service.db.session() as s:
        require_ats_admission(s, s.get(Job, job_id))
        assert may_start(s, s.get(Job, job_id), s.get(Run, run_id))
    service.db.set_setting("application_pacing:ats_limits", {"oracle": 6})
    job_id, run_id = add(service, "oracle", "queued", 6)
    with service.db.session() as s:
        assert may_start(s, s.get(Job, job_id), s.get(Run, run_id))
