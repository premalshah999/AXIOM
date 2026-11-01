"""A listed job whose location is a bare city is US: the listing says so (2026-11-01).

Workday rows name only "Arlington" or "Santa Clara"; the board screen read the country from the posting's own data and
stored a listing for it. The classification gate was handed the job without that country and refused 46 of the 104
unranked listed jobs as non-US, and the cached rejection hid them from every later pass."""

import asyncio
from datetime import UTC, datetime

from jobpilot.db import Job, JobListing
from jobpilot.features.discovery import Capacity
from jobpilot.features.discovery.classify import classify
from jobpilot.features.discovery.policy import DiscoveryPolicy, gate
from jobpilot.features.discovery.readiness import discover_due
from jobpilot.workers import classification_inputs

PROFILE = {"target_roles": ["Software Engineer"], "accept_experience_years": 3}


def listed_job(service, city="Arlington", listed=True):
    with service.db.exclusive() as s:
        job = Job(identity="c1", url="https://example.test/jobs/c1-123456", title="Software Engineer I",
                  company="ExampleSoft", location=city, ats="workday", status="new",
                  description="Build software with Python and SQL. " * 30)
        s.add(job)
        s.flush()
        if listed:
            now = datetime.now(UTC).isoformat()
            s.add(JobListing(job_id=job.id, source_id="s1", posted_at=now, date_basis="Employer startDate", location=city,
                             career_level="Early career", match_reason="x", last_seen=now))
        return job.id


def test_the_ranker_is_handed_the_listings_country(service):
    jid = listed_job(service)
    with service.db.session() as s:
        [job] = classification_inputs(s, [jid])
    assert job["country"] == "US"
    assert not gate(job, DiscoveryPolicy())
    assert gate({**job, "country": ""}, DiscoveryPolicy()) == "non_us_or_unknown"  # what the gate used to see


def test_a_cached_gate_rejection_is_read_again_once_the_gate_passes(service):
    jid = listed_job(service)
    service.db.set_setting("profile", PROFILE)
    policy = DiscoveryPolicy()
    with service.db.session() as s:
        [job] = classification_inputs(s, [jid])
    bare = {**job, "country": ""}

    async def never(ids):
        raise AssertionError("gated jobs cost no model call")

    asyncio.run(classify(service.db, [bare], never, policy, PROFILE))  # the old call: rejected non_us_or_unknown

    seen = []

    async def rank(ids):
        seen.extend(ids)
        return {i: {"score": 70, "eligible": True, "reason": "fit", "resume_id": None} for i in ids}

    report = asyncio.run(classify(service.db, [job], rank, policy, PROFILE))
    assert seen == [jid] and report["classified"] == 1


def test_a_still_refused_job_stays_cached(service):
    jid = listed_job(service, city="Toronto, Canada")
    with service.db.session() as s:
        [job] = classification_inputs(s, [jid])
    calls = []

    async def rank(ids):
        calls.append(ids)
        return {}

    for _ in range(2):
        report = asyncio.run(classify(service.db, [job], rank, DiscoveryPolicy(), PROFILE))
    assert not calls and report["cached"] == 1


def test_the_planner_ranks_a_job_the_gate_once_refused(service):
    jid = listed_job(service)
    service.db.set_setting("profile", PROFILE)
    policy = DiscoveryPolicy(owners=6)
    with service.db.session() as s:
        [job] = classification_inputs(s, [jid])

    async def never(ids):
        raise AssertionError

    asyncio.run(classify(service.db, [{**job, "country": ""}], never, policy, PROFILE))
    work = discover_due(service.db, policy, Capacity(owners_total=6, owners_busy=0))
    assert [jid] in work.classify
