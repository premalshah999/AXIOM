"""The Batch page counted 718 postings "not yet ranked" while the classification plan held none (2026-11-01).

It counted every posting without a classification row; jobs ranked by the older path and postings the gate refuses
(seniority, experience, a PhD) never get one. The count is what the plan will rank."""

from jobpilot.db import Job
from jobpilot.features.discovery.policy import DiscoveryPolicy
from jobpilot.features.discovery.readiness import discover_due, status
from jobpilot.features.discovery import Capacity


def add(service, ident, **fields):
    with service.db.exclusive() as s:
        job = Job(identity=ident, url=f"https://example.test/jobs/{ident}-123456", company="Co" + ident, location="New York, NY, United States",
                  ats="greenhouse", description="Build software. " * 50, **fields)
        s.add(job)
        s.flush()
        jid = job.id
    service.db.set_setting("job_origin:" + jid, {"kind": "manual"})
    return jid


def test_only_what_the_plan_will_rank_is_counted(service):
    service.db.set_setting("profile", {"target_roles": ["Software Engineer"]})
    fresh_job = add(service, "a", title="Software Engineer I", status="new")
    add(service, "b", title="Software Engineer I", status="skipped", score=30)  # ranked by the older path
    add(service, "c", title="Senior Software Engineer", status="new")  # the gate refuses it
    policy = DiscoveryPolicy(owners=6, early_career_only=True)
    shown = status(service.db, policy)["classification"]["unclassified"]
    planned = [j for batch in discover_due(service.db, policy, Capacity(owners_total=6, owners_busy=0)).classify for j in batch]
    assert planned == [fresh_job] and shown == 1
