"""An answered question always resumes its application, or says why it did not (onsemi 2026-10-28, Mattel 2026-10-26).

Both jobs were answered; the resume was refused because the three starts were used (the run that asked the question is
one of them), the refusal was swallowed, and the one-shot wake stayed "delivered" with no event and no new run."""

from datetime import UTC, datetime, timedelta

import pytest

from jobpilot.admission import QueueBlocked
from jobpilot.application_budget import ANSWER_TRIES, require_allowance
from jobpilot.db import Event, Job, Review, Run, Setting
from sqlalchemy import select


def seed(service, starts=3):
    old = datetime.now(UTC) - timedelta(hours=3)
    with service.db.exclusive() as s:
        s.get(Setting, "control").value = {"paused": False, "auto_submit": True}
        s.add(Setting(key="application_budget", value={"max_seconds": None}))  # production runs uncapped
        job = Job(identity="ans-1", url="https://acme.example/jobs/1", company="Acme", title="Data Analyst", ats="custom",
                  status="waiting_answer")
        s.add(job)
        s.flush()
        for i in range(starts):
            asking = i == starts - 1
            stamp = (old + timedelta(minutes=i)).isoformat()
            run = Run(job_id=job.id, state="waiting_answer" if asking else "waiting_browser", mode="submit",
                      packet={"match_approved": True}, started_at=stamp, created_at=stamp,
                      reason="Required profile information is missing: Company website")
            s.add(run)
        s.flush()
        asked = run
        review = Review(run_id=asked.id, question="Please specify the Company Website", key="k1", answer="https://x.example",
                        options=[], context={})
        s.add(review)
        s.flush()
        return job.id, asked.id, review.id


def stub_queue(service, monkeypatch, refuse=None):
    """service.queue with the real start-limit guard, and no browser."""
    calls = []

    async def queue(job_id, mode, resume_id=None, **kwargs):
        calls.append(job_id)
        if refuse:
            raise refuse
        with service.db.session() as s:
            require_allowance(s, job_id)
        return {"id": "fresh-run"}

    monkeypatch.setattr(service, "queue", queue)
    return calls


def events(service, run_id, kind):
    with service.db.session() as s:
        return list(s.scalars(select(Event).where(Event.run_id == run_id, Event.kind == kind)))


async def test_an_answer_earns_a_fresh_try_when_the_starts_are_used(service, monkeypatch):
    job_id, run_id, review_id = seed(service)
    with service.db.session() as s, pytest.raises(QueueBlocked, match="two retries"):
        require_allowance(s, job_id)
    calls = stub_queue(service, monkeypatch)
    result = await service.resume_review(review_id)  # the outbox's durable delivery
    assert result["run_id"] == "fresh-run" and len(calls) == 2  # refused once, granted, started
    assert len(events(service, run_id, "fresh_try")) == 1
    with service.db.session() as s:
        assert s.get(Setting, "application_budget_reset:" + job_id).value["answer_grants"] == 1


async def test_a_duplicate_protection_is_still_refused_and_says_so(service, monkeypatch):
    job_id, run_id, review_id = seed(service)
    stub_queue(service, monkeypatch, QueueBlocked(
        "This job is submitted or has an uncertain submission. Reconcile it first.", outcome="protected",
        code="submitted_or_uncertain"))
    result = await service.resume_review(review_id)  # final: no retry, but never silent
    assert "run_id" not in result
    assert events(service, run_id, "resume_refused")[0].data["code"] == "submitted_or_uncertain"
    with service.db.session() as s:
        assert "did not restart" in s.get(Run, run_id).reason


async def test_a_refusal_that_can_clear_re_arms_the_wake(service, monkeypatch):
    job_id, run_id, review_id = seed(service, starts=1)
    stub_queue(service, monkeypatch, QueueBlocked("Employer already has 3 reserved applications", code="company_limit"))
    with pytest.raises(QueueBlocked) as raised:
        await service.resume_review(review_id)  # the outbox row is retried, not delivered
    assert raised.value.outcome == "deferred"
    assert events(service, run_id, "resume_refused")[0].data["retried"] is True
    # the owner's own click path (not durable) reports it and keeps the wake the answer already queued
    result = await service.resume_review(review_id, {"resolved": True, "message": "Saved."})
    assert "continues on its own" in result["message"]


async def test_answers_alone_cannot_retry_a_job_for_ever(service, monkeypatch):
    job_id, run_id, review_id = seed(service)
    service.db.set_setting(
        "application_budget_reset:" + job_id,
        {"at": (datetime.now(UTC) - timedelta(days=2)).isoformat(), "answer_grants": ANSWER_TRIES},
    )
    stub_queue(service, monkeypatch)
    with pytest.raises(QueueBlocked) as raised:
        await service.resume_review(review_id)
    assert raised.value.code == "application_retry_limit"
    assert len(events(service, run_id, "fresh_try_refused")) == 1
