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


def test_one_application_at_a_time_per_oracle_site(service):
    """Dell (2026-10-23): four runs on one Oracle site requested codes at once; none could be told apart and Oracle
    locked verification. A second job on the same site waits; another employer's site does not."""
    from jobpilot.application_pacing import may_start

    def job_on(host, n, state):
        with service.db.exclusive() as s:
            job = Job(identity=f"site-{n}", url=f"https://{host}/hcmUI/CandidateExperience/en/sites/CX/job/{n}",
                      company="Dell", title="Engineer", ats="oracle")
            s.add(job)
            s.flush()
            run = Run(job_id=job.id, state=state, mode="submit", packet={}, created_at=datetime.now(UTC).isoformat())
            s.add(run)
            s.flush()
            return job.id, run.id

    job_on("dell.fa.ocs.oraclecloud.com", 1, "running")
    waiting_job, waiting_run = job_on("dell.fa.ocs.oraclecloud.com", 2, "queued")
    other_job, other_run = job_on("acme.fa.us2.oraclecloud.com", 3, "queued")
    with service.db.session() as s:
        with pytest.raises(QueueBlocked) as blocked:
            require_ats_admission(s, s.get(Job, waiting_job), run_id=waiting_run)
        assert blocked.value.outcome == "deferred"
        assert not may_start(s, s.get(Job, waiting_job), s.get(Run, waiting_run))
        require_ats_admission(s, s.get(Job, other_job), run_id=other_run)
        assert may_start(s, s.get(Job, other_job), s.get(Run, other_run))
