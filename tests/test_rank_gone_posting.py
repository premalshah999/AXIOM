"""A posting the employer removed is 'closed' with a reason, not an HTTP 500; a model failure names itself.

omegahires.applytojob.com, jobs.ascension.org and externalcareers-ohsu.icims.com postings answered 404/410 and
POST /api/jobs/{id}/rank returned 500 for each; other clicks returned 409 with an empty detail (2026-10-28)."""

import asyncio

import httpx
import pytest

from jobpilot import discovery
from jobpilot.db import Job, Resume
from jobpilot.models import AnswerModelTimeout
from jobpilot.schemas import JobInput


def seed(service, n=1, description=""):
    with service.db.exclusive() as s:
        if not s.get(Resume, "r1"):
            s.add(Resume(id="r1", name="Main", filename="main.pdf", sha256="x" * 64, text="Python, SQL"))
    return [
        discovery.add_job(
            service.db,
            JobInput(url=f"https://job-boards.greenhouse.io/acme/jobs/{900 + i}", title="Software Engineer I",
                     company="Acme", description=description),
            origin="manual",
        )[0]["id"]
        for i in range(n)
    ]


def gone(code):
    async def fetch(url, **kwargs):
        request = httpx.Request("GET", url)
        raise httpx.HTTPStatusError("gone", request=request, response=httpx.Response(code, request=request))

    return fetch


async def no_api(db, job_id, url):
    return None


def offline(monkeypatch, code):
    monkeypatch.setattr(discovery, "fetch", gone(code))
    monkeypatch.setattr(discovery, "posting_api_text", no_api)


@pytest.mark.parametrize("code", [404, 410])
def test_rank_endpoint_closes_a_removed_posting(client, service, monkeypatch, code):
    offline(monkeypatch, code)
    [job_id] = seed(service)
    r = client.post(f"/api/jobs/{job_id}/rank", json={})
    assert r.status_code == 200, r.text
    assert r.json()["eligible"] is False and str(code) in r.json()["reason"]
    with service.db.session() as s:
        job = s.get(Job, job_id)
        assert job.status == "closed" and f"HTTP {code}" in job.reason and job.score == 0


def test_other_fetch_failures_answer_with_a_message_and_leave_the_job(client, service, monkeypatch):
    offline(monkeypatch, 503)
    [job_id] = seed(service)
    r = client.post(f"/api/jobs/{job_id}/rank", json={})
    assert r.status_code == 409 and "HTTP 503" in r.json()["detail"]
    with service.db.session() as s:
        assert s.get(Job, job_id).status == "new"


def test_a_model_timeout_names_itself_instead_of_an_empty_409(client, service, monkeypatch):
    async def structured(*args, **kwargs):
        raise AnswerModelTimeout()

    monkeypatch.setattr(discovery, "structured", structured)
    [job_id] = seed(service, description="Build services in Python. " * 30)
    r = client.post(f"/api/jobs/{job_id}/rank", json={})
    assert r.status_code == 409 and "timed out" in r.json()["detail"]


def test_a_batch_closes_removed_postings_and_still_ranks_the_rest(service, monkeypatch):
    [live] = seed(service, 1, description="Build services in Python. " * 30)
    # A second posting with no text; the fetch of it answers 404.
    [dead] = [
        discovery.add_job(
            service.db,
            JobInput(url="https://job-boards.greenhouse.io/acme/jobs/777", title="Data Engineer I", company="Acme"),
            origin="manual",
        )[0]["id"]
    ]
    offline(monkeypatch, 404)
    seen = {}

    async def structured(config, db, output_type, instructions, prompt, *args, **kwargs):
        seen["jobs"] = [j["job_id"] for j in __import__("json").loads(prompt)["jobs"]]
        return output_type(jobs=[{"job_id": live, "score": 80, "eligible": True, "resume_id": "r1", "reason": "fit"}])

    monkeypatch.setattr(discovery, "structured", structured)
    out = {r["job_id"]: r for r in asyncio.run(discovery.rank_batch(service.db, service.config, [live, dead]))}
    assert seen["jobs"] == [live] and out[dead]["closed"] and out[live]["score"] == 80
    with service.db.session() as s:
        assert s.get(Job, dead).status == "closed"
        assert s.get(Job, live).status in {"ready", "review", "skipped"}


def test_a_transient_fetch_failure_defers_only_that_job(service, monkeypatch):
    [live] = seed(service, 1, description="Build services in Python. " * 30)
    [flaky] = [
        discovery.add_job(
            service.db,
            JobInput(url="https://job-boards.greenhouse.io/acme/jobs/778", title="Data Engineer I", company="Acme"),
            origin="manual",
        )[0]["id"]
    ]
    offline(monkeypatch, 503)

    async def structured(config, db, output_type, instructions, prompt, *args, **kwargs):
        return output_type(jobs=[{"job_id": live, "score": 80, "eligible": True, "resume_id": "r1", "reason": "fit"}])

    monkeypatch.setattr(discovery, "structured", structured)
    out = {r["job_id"]: r for r in asyncio.run(discovery.rank_batch(service.db, service.config, [live, flaky]))}
    assert "HTTP 503" in out[flaky]["deferred"] and out[live]["score"] == 80
    with service.db.session() as s:
        assert s.get(Job, flaky).status == "new"
