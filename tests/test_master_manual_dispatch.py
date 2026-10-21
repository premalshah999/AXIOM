from datetime import UTC, datetime, timedelta

from jobpilot.db import Job, Run
from jobpilot.features.discovery.policy import DiscoveryPolicy
from jobpilot.features.discovery.readiness import ready_manual_job_ids
from jobpilot.features.discovery.tables import DcDeferral


def seed(service, name, **kwargs):
    with service.db.exclusive() as session:
        job = Job(identity=name, url=f"https://example.test/jobs/{name}-123456", title="Software Engineer",
                  company=name, location="New York, NY, United States", ats="workday", status="ready",
                  score=90, description="Build software. " * 50, **kwargs)
        session.add(job)
        session.flush()
        jid = job.id
    service.db.set_setting("job_origin:" + jid, {"kind": "manual"})
    return jid


def test_master_import_needs_no_tailored_artifact_but_keeps_guards(service):
    good = seed(service, "good")
    attempted = seed(service, "attempted")
    deferred = seed(service, "deferred")
    paused = seed(service, "paused")
    senior = seed(service, "senior")
    automatic = seed(service, "automatic")
    service.db.set_setting("job_origin:" + automatic, {"kind": "discovery"})
    service.db.set_setting("job_control:" + paused, {"state": "paused"})
    with service.db.exclusive() as session:
        session.add(Run(job_id=attempted, state="confirmed", mode="submit"))
        session.add(DcDeferral(job_id=deferred, reason="fixture", not_before=(datetime.now(UTC)+timedelta(hours=1)).isoformat()))
        session.get(Job, senior).title = "Senior Software Engineer"
    assert ready_manual_job_ids(service.db, DiscoveryPolicy(early_career_only=True)) == {good}


def test_ineligible_old_scores_do_not_block_new_classification(service):
    from jobpilot.features.discovery.readiness import _eligible

    profile = {"target_roles": ["Software Development Engineer"]}
    base = {"url": "https://example.test/jobs/123456", "description": "Build software. " * 50,
            "location": "New York, NY, United States", "status": "ready", "score": 90,
            "resume_id": None}
    jobs = [{**base, "id": "good", "title": "Software Engineer"},
            {**base, "id": "offrole", "title": "Data Analyst"},
            {**base, "id": "senior", "title": "Senior Software Engineer"},
            {**base, "id": "foreign", "title": "Software Engineer", "location": "London, UK"}]
    assert set(_eligible(service.db, jobs, profile, DiscoveryPolicy(early_career_only=True))) == {"good"}
