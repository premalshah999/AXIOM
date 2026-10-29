"""The readiness status reflects the application resume policy (2026-10-28).

Policy mode `master` builds no tailored resume, yet the status said "Tailoring: 31 eligible jobs are waiting for a
tailored resume" and ready_to_apply read 0 beside 260 ready jobs."""

from jobpilot.db import Job, JobListing, Run
from jobpilot.features.discovery.policy import DiscoveryPolicy
from jobpilot.features.discovery.readiness import discover_due, ready_applications, status


def seed(service, name, status_="ready", score=80):
    with service.db.exclusive() as s:
        job = Job(identity=name, url=f"https://example.test/jobs/{name}-123456", title="Software Engineer",
                  company=name, location="New York, NY, United States", ats="greenhouse", status=status_,
                  score=score, description="Build software. " * 50, resume_id="r1" if status_ == "ready" else None)
        s.add(job)
        s.flush()
        jid = job.id
        s.add(JobListing(job_id=jid, source_id="src", posted_at=_now(), date_basis="verified",
                         location="New York, NY", country="US", career_level="entry", match_reason="fixture"))
    return jid


def _now():
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat()


def test_master_policy_has_no_tailoring_bottleneck_and_counts_ready_jobs(service):
    service.db.set_setting("application_resume_policy", {"mode": "master", "resume_id": "r1"})
    ids = [seed(service, f"ok{i}") for i in range(3)]
    out = status(service.db, DiscoveryPolicy(minimum_fit=45))
    assert out["resume_policy"] == "master"
    assert out["ready_to_apply"] == 3 and out["ready_jobs"] == 3
    assert "Tailoring" not in out["bottleneck"] and "3 applications are ready" in out["bottleneck"]
    assert {x.job_id for x in ready_applications(service.db, DiscoveryPolicy(minimum_fit=45))} == set(ids)


def test_master_policy_plans_no_tailoring(service):
    service.db.set_setting("application_resume_policy", {"mode": "master", "resume_id": "r1"})
    for i in range(3):
        seed(service, f"w{i}")
    due = discover_due(service.db, DiscoveryPolicy(minimum_fit=45))
    assert due.tailor == [] and any("master" in r for r in due.reasons)


def test_tailored_policy_still_reports_the_tailoring_wait(service):
    service.db.set_setting("application_resume_policy", {"mode": "tailored"})
    seed(service, "a")
    out = status(service.db, DiscoveryPolicy(minimum_fit=45))
    assert out["resume_policy"] == "tailored" and out["ready_to_apply"] == 0
    assert out["bottleneck"].startswith("Tailoring: 1 eligible")


def test_attempted_and_unready_jobs_are_not_ready_to_apply(service):
    service.db.set_setting("application_resume_policy", {"mode": "master", "resume_id": "r1"})
    done, skipped, fresh = seed(service, "done"), seed(service, "sk", "skipped", 60), seed(service, "ok")
    with service.db.exclusive() as s:
        s.add(Run(job_id=done, state="confirmed", mode="submit"))
    out = status(service.db, DiscoveryPolicy(minimum_fit=45))
    assert out["ready_to_apply"] == 1 and out["ready_jobs"] == 1
    assert {x.job_id for x in ready_applications(service.db, DiscoveryPolicy(minimum_fit=45))} == {fresh}


def test_closed_postings_are_not_candidates(service):
    service.db.set_setting("application_resume_policy", {"mode": "master", "resume_id": "r1"})
    seed(service, "gone", "closed", 0)
    assert status(service.db, DiscoveryPolicy(minimum_fit=45))["fresh_jobs"] == 0
