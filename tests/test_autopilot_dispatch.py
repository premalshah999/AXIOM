"""The autopilot starts every Ready job queue admission would take, and keeps why it did not (2026-10-29: 143 Ready jobs
under every limit were refused by the autopilot while the owner's Apply admitted 142 of them at once)."""
from types import SimpleNamespace

from jobpilot import autopilot, execution_services
from jobpilot.db import Job, JobListing, Run
from jobpilot.jobboard import BoardSettings, settings as board_settings


def add_job(service, key, *, location="Remote", company="Example", ats="oracle", status="ready", score=90,
            listing=None, url=None):
    with service.db.exclusive() as s:
        job = Job(identity="dispatch-" + key, url=url or f"https://{key}.example.test/job/12345", ats=ats,
                  title="Software Engineer", company=company, location=location, status=status, score=score,
                  description="Build and ship software. " * 40)
        s.add(job)
        s.flush()
        if listing:
            s.add(JobListing(job_id=job.id, source_id="src", posted_at="2026-10-28T00:00:00+00:00", date_basis="posted",
                             location=location, country=listing.get("country", "US"), career_level="Early career",
                             match_reason="", active=listing.get("active", True)))
        return job.id


def test_unlisted_ready_keeps_a_stale_listings_country_and_drops_a_taken_down_posting(service):
    stale = add_job(service, "stale", location="Remote-MO", listing={"country": "US"})
    gone = add_job(service, "gone", listing={"active": False})
    bare = add_job(service, "bare")
    found = {j["id"]: j for j in autopilot.unlisted_ready(service.db, board_settings(service.db), set())}
    assert set(found) == {stale, bare}
    assert found[stale]["country"] == "US" and found[stale]["career_level"] == "Early career"
    assert found[bare]["ats"] == "oracle" and found[bare]["country"] == "" and found[bare]["career_level"] == "Imported"


def test_remember_rejection_keeps_the_latest_reason_per_job_and_is_bounded():
    batch = {}
    autopilot.remember_rejection(batch, "a", "first", "queue_guard", "excluded", at="t1")
    autopilot.remember_rejection(batch, "a", "second", "company_limit", "deferred", at="t2")
    assert batch["rejections"]["a"]["reason"] == "second" and batch["rejections"]["a"]["at"] == "t2"
    for n in range(autopilot.REJECTIONS_KEPT + 5):
        autopilot.remember_rejection(batch, f"j{n}", "x")
    assert len(batch["rejections"]) == autopilot.REJECTIONS_KEPT
    assert "a" not in batch["rejections"] and f"j{autopilot.REJECTIONS_KEPT + 4}" in batch["rejections"]


async def run_tick(service, monkeypatch, queue):
    service.dispatch = lambda *args: None
    service.db.set_setting("control", {"auto_submit": True})
    service.db.set_setting("execution_services", {"enabled": True, "version": 1})
    service.db.set_setting("application_resume_policy", {"mode": "master"})
    cfg = BoardSettings(auto_apply=True)
    monkeypatch.setattr(autopilot, "settings", lambda db: cfg)
    monkeypatch.setattr(autopilot, "replenish_discovery", lambda *args: None)

    async def nothing(*args, **kwargs):
        return 0

    async def prepared(service):
        return SimpleNamespace(classify=[])

    monkeypatch.setattr(autopilot, "persist_stalled", nothing)
    monkeypatch.setattr(autopilot, "resubmit_unknown", nothing)
    monkeypatch.setattr(execution_services, "prepare_due", prepared)
    monkeypatch.setattr(service, "queue", queue)
    await autopilot.tick(service)
    return service.db.get_setting("job_board_autopilot")


async def test_a_ready_job_without_a_listing_or_country_is_dispatched(service, monkeypatch):
    """"Remote" / "2 Locations" has no country: it was refused non_us_or_unknown and never started."""
    ids = [add_job(service, "r1", location="Remote"), add_job(service, "r2", location="2 Locations"),
           add_job(service, "r3", location="Remote-MO", listing={"country": "US"})]
    called = []

    async def queue(job_id, *args, **kwargs):
        called.append(job_id)
        return {"id": "run-" + job_id}

    batch = await run_tick(service, monkeypatch, queue)
    assert sorted(called) == sorted(ids)
    assert batch["queued"] == 3 and not batch.get("rejections")


async def test_a_stated_foreign_location_is_still_refused_and_the_reason_is_kept(service, monkeypatch):
    foreign = add_job(service, "f1", location="Bangalore, India")
    called = []

    async def queue(job_id, *args, **kwargs):
        called.append(job_id)
        return {"id": "run-" + job_id}

    batch = await run_tick(service, monkeypatch, queue)
    assert called == []
    assert batch["rejections"][foreign]["reason"] == "non_us_or_unknown"


async def occupy(service, host="busy.example.test"):
    with service.db.exclusive() as s:
        busy = Job(identity="busy-run", url=f"https://{host}/job/1", ats="oracle", title="Engineer",
                   company="Busy", status="running")
        s.add(busy)
        s.flush()
        s.add(Run(id="busy-run", job_id=busy.id, state="running", mode="submit", packet={"worker_slot": 1}))


async def test_a_busy_employer_waits_with_its_reason_while_others_start(service, monkeypatch):
    await occupy(service)
    waiting = add_job(service, "busy", url="https://busy.example.test/job/2")
    other = add_job(service, "other")
    called = []

    async def queue(job_id, *args, **kwargs):
        called.append(job_id)
        return {"id": "run-" + job_id}

    batch = await run_tick(service, monkeypatch, queue)
    assert called == [other]  # The occupied employer is not asked at all.
    assert "employer" in batch["rejections"][waiting]["reason"]


async def test_a_full_pool_ends_the_pass_and_holds_nothing_back(service, monkeypatch):
    from jobpilot.admission import QueueBlocked

    ids = [add_job(service, f"full{n}") for n in range(4)]
    called = []

    async def queue(job_id, *args, **kwargs):
        called.append(job_id)
        raise QueueBlocked("This worker pool is busy", outcome="deferred", code="worker_capacity",
                           not_before="2999-01-01T00:00:00+00:00")

    batch = await run_tick(service, monkeypatch, queue)
    assert len(called) == 1 and called[0] in ids  # The first refusal ends the pass.
    assert not batch.get("deferred")  # A pool that frees up is retried on the next tick, not after a wait.


async def test_owner_requests_waiting_on_capacity_retry_on_the_next_pass(service, monkeypatch):
    from jobpilot import owner_apply
    from jobpilot.admission import QueueBlocked
    from jobpilot.db import now

    refuse = [True]
    seen = []

    async def queue(job_id, *args, **kwargs):
        seen.append(job_id)
        if refuse[0]:
            raise QueueBlocked("busy", outcome="deferred", code="worker_capacity",
                               not_before="2999-01-01T00:00:00+00:00")
        return {"id": "run-" + job_id}

    monkeypatch.setattr(service, "queue", queue)
    ids = [add_job(service, f"o{n}") for n in range(3)]
    for job_id in ids:
        result = await owner_apply.start(service, job_id)
        assert result["state"] == "waiting"
    seen.clear()
    waiting = service.db.get_setting(owner_apply.KEY + ids[0])
    assert waiting["not_before"] <= now()  # Retried at the next pass, not after a fixed wait.
    assert await owner_apply.due(service, 5) == 0
    assert len(seen) == 1  # A full pool refuses the rest alike: one admission, not one per request.
    refuse[0] = False
    assert await owner_apply.due(service, 5) == 3
    assert service.db.get_setting(owner_apply.KEY + ids[0]) is None


async def test_owner_requests_blocked_by_one_employer_do_not_starve_the_rest(service, monkeypatch):
    from jobpilot import owner_apply
    from jobpilot.admission import QueueBlocked

    blocked = [add_job(service, f"b{n}") for n in range(owner_apply.TRIES)]
    free = add_job(service, "free")
    started, opened = [], [False]

    async def queue(job_id, *args, **kwargs):
        if job_id in blocked or not opened[0]:
            raise QueueBlocked("employer", outcome="deferred", code="employer_busy",
                               not_before="2999-01-01T00:00:00+00:00")
        started.append(job_id)
        return {"id": "run-" + job_id}

    monkeypatch.setattr(service, "queue", queue)
    for job_id in blocked:
        await owner_apply.start(service, job_id)
    await owner_apply.start(service, free)
    opened[0] = True
    assert started == []
    for _ in range(2):  # The first pass tries TRIES requests; the least recently tried go last on the next.
        await owner_apply.due(service, 1)
    assert started == [free]


async def test_the_threshold_readmission_runs_once_per_policy_revision(service, monkeypatch):
    import jobpilot.discovery as discovery

    calls = []
    monkeypatch.setattr(discovery, "readmit_by_threshold", lambda db: calls.append(1) or 0)

    async def queue(job_id, *args, **kwargs):
        return {"id": "run-" + job_id}

    await run_tick(service, monkeypatch, queue)
    await autopilot.tick(service)
    assert calls == [1]
    service.db.set_setting("profile", {"minimum_fit": 40})
    await autopilot.tick(service)
    assert calls == [1, 1]


async def test_fresh_ready_jobs_take_the_slots_before_retries_of_failed_jobs(service, monkeypatch):
    from jobpilot import owner_apply

    seen = {}

    async def due(service, free, retries=None):
        seen.setdefault("retries", retries)  # The first call is the one ahead of the fresh jobs.
        return 0

    monkeypatch.setattr(owner_apply, "due", due)

    async def queue(job_id, *args, **kwargs):
        return {"id": "run-" + job_id}

    add_job(service, "fresh1")
    await run_tick(service, monkeypatch, queue)
    assert seen["retries"] == 0  # With a fresh job waiting, no retry starts ahead of it.
