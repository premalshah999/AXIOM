"""POST /api/jobs/apply-many: the owner's Apply for several jobs at once (owner, 2026-10-22: "for unfit applications
there should be an option of ticking it or select all, to apply to a few at once rather than opening every single
one"). Each job takes the one-click path, so every duplicate-submission guard still decides it. Synthetic data."""

import pytest
from sqlalchemy import func, select

from jobpilot.db import Job, Run

pytestmark = pytest.mark.integration


@pytest.fixture
def workers(prepared, service):
    """A dispatcher that accepts runs without a browser, automatic submission on."""
    dispatched = []

    async def dispatch(run_id):
        dispatched.append(run_id)

    service.dispatch = dispatch
    service.db.set_setting("control", {"paused": False, "auto_submit": True})
    return dispatched


def add_job(service, n, *, status="skipped", score=35, url=None):
    with service.db.exclusive() as s:
        s.add(
            Job(
                id=f"unfit{n}",
                identity=f"unfit-{n}",
                url=url or f"https://boards.greenhouse.io/acme{n}/jobs/{4000 + n}",
                title=f"Data Analyst {n}",
                company=f"Acme Example {n}",
                ats="greenhouse",
                status=status,
                score=score,
                reason="Below the minimum fit",
            )
        )
    return f"unfit{n}"


def test_below_fit_jobs_are_approved_and_queued_with_a_result_each(client, service, workers):
    ids = [add_job(service, n) for n in range(3)]
    done = add_job(service, 9, status="confirmed", score=90)
    answer = client.post("/api/jobs/apply-many", json={"job_ids": [*ids, ids[0], done, "no-such-job"]})
    assert answer.status_code == 200, answer.text
    body = answer.json()
    outcomes = {r["job_id"]: r["outcome"] for r in body["results"]}
    assert outcomes == {**dict.fromkeys(ids, "queued"), done: "already", "no-such-job": "refused"}
    assert body["summary"] == {"queued": 3, "already": 1, "refused": 1}
    assert len(workers) == 3  # one run each, the repeated id included once
    with service.db.session() as s:
        assert {j.status for j in s.scalars(select(Job).where(Job.id.in_(ids)))} == {"queued"}
        assert all(j.reason.startswith("Owner approved: ") for j in s.scalars(select(Job).where(Job.id.in_(ids))))


def test_a_second_listing_of_the_same_posting_is_refused_not_submitted_twice(client, service, workers):
    first = add_job(service, 1, url="https://boards.greenhouse.io/acme/jobs/4242")
    alias = add_job(service, 2, url="https://boards.greenhouse.io/acme/jobs/4242?utm_source=feed")
    body = client.post("/api/jobs/apply-many", json={"job_ids": [first, alias]}).json()
    result = {r["job_id"]: r for r in body["results"]}
    assert result[first]["outcome"] == "queued"
    assert result[alias]["outcome"] == "refused" and result[alias]["code"] == "active_run"
    with service.db.session() as s:
        assert s.scalar(select(func.count()).select_from(Run)) == 1


def test_an_applied_job_in_the_selection_is_never_queued_again(client, service, workers):
    job_id = add_job(service, 3, status="ready", score=90)
    with service.db.exclusive() as s:
        s.add(Run(job_id=job_id, state="confirmed", mode="submit", packet={}))
    body = client.post("/api/jobs/apply-many", json={"job_ids": [job_id]}).json()
    assert body["results"][0]["outcome"] == "refused"
    assert body["results"][0]["code"] == "submitted_or_uncertain" and not workers


def test_apply_many_is_bounded_and_needs_workers(client, service):
    assert client.post("/api/jobs/apply-many", json={"job_ids": []}).status_code == 422
    assert client.post("/api/jobs/apply-many", json={"job_ids": [f"j{n}" for n in range(51)]}).status_code == 422
    answer = client.post("/api/jobs/apply-many", json={"job_ids": ["one"]})
    assert answer.status_code == 400 and answer.json() == {"detail": "Workers are disabled"}
