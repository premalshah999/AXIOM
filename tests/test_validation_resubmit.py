"""Howmet (Oracle) 2026-10-14: after the employer refuses a commit with explicit field errors and
the page is repaired, the resubmit keeps the one reservation (bounded retry), never a second one."""

from types import SimpleNamespace

import pytest

from jobpilot.browser import BrowserEngine

REFUSAL = {"category": "validation", "missing_fields": ["Major"]}


def engine():
    calls = []
    service = SimpleNamespace(
        reserve_submission=lambda run_id: calls.append(("reserve", run_id)),
        authorize_validation_retry=lambda run_id, rejection: calls.append(
            ("retry", run_id, rejection["missing_fields"][0])
        ),
    )
    e = object.__new__(BrowserEngine)
    e.service, e.run_id = service, "r1"
    return e, calls


async def test_first_commit_reserves():
    e, calls = engine()
    await e.reserve_commit()
    assert calls == [("reserve", "r1")]


async def test_repaired_refusal_resubmits_under_the_retry_authorization_once():
    e, calls = engine()
    e.validation_refusal = REFUSAL
    await e.reserve_commit()
    assert calls == [("retry", "r1", "Major")] and e.validation_refusal is None
    await e.reserve_commit()  # Any later commit is a fresh reservation request (refused by the service).
    assert calls[-1] == ("reserve", "r1")


async def test_a_refused_retry_keeps_the_refusal():
    e, _ = engine()

    def refuse(run_id, rejection):
        raise ValueError("Application validation repair limit reached")

    e.service.authorize_validation_retry = refuse
    e.validation_refusal = REFUSAL
    with pytest.raises(ValueError):
        await e.reserve_commit()
    assert e.validation_refusal == REFUSAL


def test_a_released_reservation_is_taken_back_by_the_repaired_resubmit(service):
    # Westinghouse (2026-10-27): the refused commit released its reservation, and the repaired resubmit then ended
    # "This run has no authorized submission reservation".
    from jobpilot.db import Job, Run

    service.db.set_setting("control", {"paused": False, "auto_submit": True})
    with service.db.exclusive() as s:
        job = Job(identity="vr-1", url="https://careers.example.test/j/1", company="Acme", title="Engineer", ats="custom")
        s.add(job)
        s.flush()
        from jobpilot.db import now

        run = Run(job_id=job.id, state="running", mode="submit", packet={}, started_at=now())
        s.add(run)
        s.flush()
        run_id = run.id
    service.authorize_validation_retry(run_id, REFUSAL)
    with service.db.session() as s:
        assert s.get(Run, run_id).state == "submitting"
