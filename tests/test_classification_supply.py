"""Ranking keeps enough eligible supply ahead of the workers (2026-10-28).

3,225 fresh postings sat unclassified: the target was ~4 eligible jobs per owner, the plan 48 postings, and the
workflow ranked 6 per model call. The plan now scales with free slots and the share of postings that qualify, and
batches ride one model call each, off the shared event loop."""

import asyncio
import json
import threading

from jobpilot import discovery
from jobpilot.db import Job, Resume
from jobpilot.features.discovery import Capacity, existing_rank_adapter
from jobpilot.features.discovery.policy import DiscoveryPolicy
from jobpilot.features.discovery.readiness import discover_due


def new_jobs(service, n):
    ids = []
    with service.db.exclusive() as s:
        for i in range(n):
            job = Job(identity=f"n{i}", url=f"https://example.test/jobs/n{i}-123456", title="Software Engineer",
                      company=f"Co{i}", location="New York, NY, United States", ats="greenhouse", status="new",
                      description="Build software. " * 50)
            s.add(job)
            s.flush()
            ids.append(job.id)
    for jid in ids:
        service.db.set_setting("job_origin:" + jid, {"kind": "manual"})


def planned(service, busy, **policy):
    service.db.set_setting("profile", {"target_roles": ["Software Engineer"]})
    work = discover_due(service.db, DiscoveryPolicy(owners=6, **policy), Capacity(owners_total=6, owners_busy=busy))
    return [len(batch) for batch in work.classify]


def test_idle_workers_plan_more_supply_than_busy_ones(service):
    new_jobs(service, 200)
    busy, idle = planned(service, 6), planned(service, 0)
    assert sum(idle) > sum(busy) >= 24
    assert all(size <= 24 for size in idle + busy)


def test_the_plan_is_bounded_per_tick(service):
    new_jobs(service, 400)
    assert sum(planned(service, 0, classify_lookahead=20)) <= 72


def test_the_plan_covers_the_shortfall_at_the_expected_yield(service):
    new_jobs(service, 400)
    # 6 owners, all free: target (6 + 6) * 4 = 48 eligible; at 35% that is 138 postings, bounded to 3 batches.
    assert sum(planned(service, 0)) == 72
    # Higher yield needs fewer postings.
    assert sum(planned(service, 0, classify_yield=1.0)) == 48


def test_the_batch_ranker_runs_off_the_event_loop(service, monkeypatch):
    with service.db.exclusive() as s:
        s.add(Resume(id="r1", name="Main", filename="main.pdf", sha256="x" * 64, text="Python, SQL"))
    new_jobs(service, 3)
    with service.db.session() as s:
        ids = [j.id for j in s.query(Job)]
    seen = {}

    async def structured(config, db, output_type, instructions, prompt, *args, **kwargs):
        seen["thread"] = threading.current_thread().name
        return output_type(jobs=[{"job_id": j["job_id"], "score": 70, "eligible": True, "resume_id": "r1",
                                  "reason": "fit"} for j in json.loads(prompt)["jobs"]])

    monkeypatch.setattr(discovery, "structured", structured)
    out = asyncio.run(existing_rank_adapter(service.db, service.config)(ids))
    assert set(out) == set(ids) and seen["thread"] != threading.main_thread().name
