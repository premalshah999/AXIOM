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


def test_jobs_the_batch_already_skipped_do_not_pause_classification(service):
    """2026-10-23: 37 eligible jobs (internships, employers at their cap) that the batch had skipped counted as a full
    pipeline; classification paused and 3,137 fresh postings stayed unranked while workers idled."""
    from jobpilot.features.discovery.readiness import discover_due

    service.db.set_setting("profile", {"target_roles": ["Software Engineer"], "include_internships": False})
    skipped = [seed(service, f"capped{i}") for i in range(30)]
    interns = [seed(service, f"intern{i}") for i in range(5)]
    with service.db.exclusive() as session:
        for jid in interns:
            session.get(Job, jid).title = "Software Engineer Intern"
        fresh = Job(identity="fresh", url="https://example.test/jobs/fresh-123456", title="Software Engineer",
                    company="Fresh", location="New York, NY, United States", ats="workday", status="new",
                    description="Build software. " * 50)
        session.add(fresh)
        session.flush()
        fresh_id = fresh.id
    service.db.set_setting("job_origin:" + fresh_id, {"kind": "manual"})
    service.db.set_setting("job_board_autopilot", {"state": "active", "considered_ids": skipped})
    work = discover_due(service.db, DiscoveryPolicy(early_career_only=True))
    assert any(fresh_id in batch for batch in work.classify), work.reasons


def test_jobs_already_scored_by_the_older_ranker_are_not_unclassified_inventory(service):
    """2026-10-25: ready jobs scored 58-82 by the older ranking path had no classification row, counted as unclassified,
    were skipped by the classifier (it ranks only new, unscored jobs), and kept discovery paused for 41 hours."""
    from jobpilot.db import Source
    from jobpilot.features.discovery.readiness import discover_due

    service.db.set_setting("profile", {"target_roles": ["Software Engineer"]})
    scored = [seed(service, f"scored{i}") for i in range(40)]
    with service.db.exclusive() as session:
        for jid in scored:
            session.get(Job, jid).status = "new"  # Legacy rows: a score but still "new" or "ready".
        session.add(Source(id="src", name="Board", kind="workday", url="https://example.test/board", cron="0 */2 * * *",
                           enabled=True, next_run="2026-01-01T00:00:00+00:00", last_run="", error="", found=1))
    work = discover_due(service.db, DiscoveryPolicy(early_career_only=True))
    assert not any(jid in batch for batch in work.classify for jid in scored)
    assert "src" in work.sources, work.reasons
