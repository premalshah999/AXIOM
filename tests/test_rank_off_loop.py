"""Ranking runs on a private event loop (2026-10-15): its synchronous profile, database and parsing
work no longer stalls the browser owners that share the main loop."""

import asyncio
import threading

from jobpilot import discovery
from jobpilot.db import Job, Resume
from jobpilot.schemas import JobInput


def seed(service):
    with service.db.exclusive() as s:
        s.add(Resume(id="r1", name="Main", filename="main.pdf", sha256="x" * 64, text="Python, PyTorch, SQL"))
    row, _ = discovery.add_job(
        service.db,
        JobInput(
            url="https://tenant.wd5.myworkdayjobs.com/External/job/Austin-TX/ML-Engineer_R1",
            title="Machine Learning Engineer I",
            company="Tenant",
            description="Build ML models in Python. US location.",
        ),
        origin="manual",
    )
    return row["id"]


def fake_structured(seen):
    async def structured(config, db, output_type, instructions, prompt, *args, **kwargs):
        seen["thread"] = threading.current_thread().name
        seen["loop"] = id(asyncio.get_running_loop())
        return output_type(score=80, eligible=True, resume_id="r1", reason="Strong fit")

    return structured


def test_rank_endpoint_ranks_on_a_private_loop(client, service, monkeypatch):
    seen = {}
    monkeypatch.setattr(discovery, "structured", fake_structured(seen))
    job_id = seed(service)
    r = client.post(f"/api/jobs/{job_id}/rank", json={})
    assert r.status_code == 200, r.text
    assert r.json()["score"] == 80
    with service.db.session() as s:
        assert s.get(Job, job_id).status in {"ready", "review"}
    # The model call ran off the main thread, on a loop of its own.
    assert seen["thread"] != threading.main_thread().name


def test_rank_in_thread_returns_the_classification(service, monkeypatch):
    seen = {}
    monkeypatch.setattr(discovery, "structured", fake_structured(seen))
    job_id = seed(service)
    result = asyncio.run(asyncio.to_thread(discovery.rank_in_thread, service.db, service.config, job_id))
    assert result["score"] == 80 and result["resume_id"] == "r1"
