"""A model key that is missing, refused or out of credit says so, keeps every job, and a new key ends it."""

import pytest

from jobpilot import startable
from jobpilot.db import Job, JobListing, Resume


def ready_job(service, key="a", title="Software Engineer", location="Denver, CO", score=90):
    with service.db.exclusive() as s:
        job = Job(identity="fresh-" + key, url=f"https://{key}.example.test/job/12345", ats="greenhouse", title=title,
                  company="ExampleSoft " + key, location=location, status="ready", score=score,
                  description="Build and ship software. " * 40)
        s.add(job)
        s.flush()
        s.add(JobListing(job_id=job.id, source_id="src", posted_at="2026-10-30T00:00:00+00:00", date_basis="posted",
                         location=location, country="US", career_level="", match_reason="", active=True))
        return job.id


def add_resume(service, name="Resume", created="2026-10-30T00:00:00+00:00"):
    with service.db.exclusive() as s:
        row = Resume(name=name, filename=name + ".pdf", sha256=name * 8, roles=[], text="Alex Example, engineer. " * 5,
                     created_at=created)
        s.add(row)
        s.flush()
        return row.id


async def test_a_missing_key_makes_jobs_wait_and_never_writes_them_off(service):
    from jobpilot.admission import QueueBlocked, outcome_for

    service.dispatch = lambda *a: None
    add_resume(service)
    service.db.set_setting("control", {"paused": False, "auto_submit": True})
    service.db.set_setting("profile", {"name": "Alex Example", "email": "alex@example.test"})
    with pytest.raises(QueueBlocked) as caught:
        await service.queue(ready_job(service, "k"), mode="submit")
    assert outcome_for(caught.value)["outcome"] == "deferred" and caught.value.code == "no_model_key"
    assert "Settings > Connections" in startable.everything_stopped(service)


def test_a_refused_key_is_named_waits_longer_and_a_new_key_ends_it_at_once(service):
    import httpx

    from jobpilot import models, provider_health

    assert models.provider_rejection(httpx.Response(401), b"{}")["rejection_category"] == "auth_rejected"
    assert models.provider_rejection(httpx.Response(403), b"{}")["rejection_category"] == "auth_rejected"
    assert models.provider_rejection(httpx.Response(402), b"{}")["rejection_category"] == "quota_exhausted"
    assert models.provider_rejection(httpx.Response(500), b"{}")["rejection_category"] == "http_error"

    config = service.config
    config.llm_provider = "deepseek"
    config.deepseek_api_key = "old-key"
    ticket = provider_health.acquire(service.db, config, 30)
    provider_health.finish(service.db, config, ticket, failure="auth_rejected")
    gate = provider_health.dispatch_gate(service.db, config)
    assert gate.limit == 0 and "refused your API key" in gate.reason
    assert provider_health.status(service.db, config)["state"] == "cooldown"
    assert provider_health.acquire(service.db, config, 30) is None

    config.deepseek_api_key = "new-key"
    assert provider_health.status(service.db, config)["state"] == "ready"
    assert provider_health.acquire(service.db, config, 30) is not None
    assert provider_health.dispatch_gate(service.db, config).limit > 0


def test_an_outage_still_cools_down_on_a_changed_key(service):
    from jobpilot import provider_health

    config = service.config
    config.llm_provider = "deepseek"
    config.deepseek_api_key = "one"
    for _ in range(2):
        provider_health.finish(service.db, config, provider_health.acquire(service.db, config, 30), failure="http_error")
    config.deepseek_api_key = "two"
    assert provider_health.status(service.db, config)["state"] == "cooldown"


