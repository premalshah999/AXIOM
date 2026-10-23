"""The application queue (owner, 2026-10-22): running applications, waiting requests in start order with their reason,
and pasted links that go straight into the queue as the owner's own picks."""

from jobpilot.admission import QueueBlocked
from jobpilot.db import Job, Run, Setting

from support_http import seed_job


def test_pasted_links_are_saved_and_queued_as_the_owners_pick(client, service, monkeypatch):
    service.dispatch = True
    picked = []

    async def fake_apply(service, job_id):
        picked.append(job_id)
        return {"job_id": job_id, "state": "queued", "run_id": "r-" + job_id[:4], "message": "Application queued."}

    monkeypatch.setattr("jobpilot.api.routes.queue.apply", fake_apply)
    monkeypatch.setattr("jobpilot.api.routes.queue.posting_api_text", lambda *a: _noop())
    answer = client.post(
        "/api/queue/links",
        json={"urls": [
            "https://boards.example.test/acme/jobs/101",
            "https://boards.example.test/acme/jobs/101 ",  # pasted twice: one job
            "https://careers-beta.example.test/jobs/7/data-engineer/job",
        ]},
    )
    assert answer.status_code == 200
    results = answer.json()["results"]
    assert [r["state"] for r in results] == ["queued", "queued"] and len(set(picked)) == 2
    with service.db.session() as s:
        for job_id in picked:
            assert s.get(Job, job_id) is not None
            assert s.get(Setting, "job_origin:" + job_id).value == {"kind": "manual"}


async def _noop():
    return None


def test_a_refused_link_reports_why_and_the_others_still_queue(client, service, monkeypatch):
    service.dispatch = True

    async def fake_apply(service, job_id):
        with service.db.session() as s:
            if s.get(Job, job_id).url.endswith("/2"):
                raise QueueBlocked("This job is submitted or has an uncertain submission. Reconcile it first.",
                                   outcome="protected")
        return {"job_id": job_id, "state": "waiting", "message": "This worker pool is busy."}

    monkeypatch.setattr("jobpilot.api.routes.queue.apply", fake_apply)
    monkeypatch.setattr("jobpilot.api.routes.queue.posting_api_text", lambda *a: _noop())
    results = client.post(
        "/api/queue/links",
        json={"urls": ["https://boards.example.test/acme/jobs/1", "https://boards.example.test/acme/jobs/2"]},
    ).json()["results"]
    assert [r["state"] for r in results] == ["waiting", "refused"]
    assert "Reconcile it first" in results[1]["message"]


def test_only_job_links_are_accepted_and_workers_must_be_on(client, service):
    assert client.post("/api/queue/links", json={"urls": ["not a link"]}).status_code == 422
    assert client.post("/api/queue/links", json={"urls": []}).status_code == 422
    service.dispatch = False
    assert client.post("/api/queue/links", json={"urls": ["https://boards.example.test/acme/jobs/9"]}).status_code == 400


def test_the_queue_lists_running_then_waiting_requests_in_start_order_and_withdraws(client, service):
    running, first, second = seed_job(service, 1), seed_job(service, 2), seed_job(service, 3)
    with service.db.exclusive() as s:
        s.add(Run(job_id=running, state="running", mode="submit", packet={}))
    service.db.set_setting("owner_apply:" + second, {"at": "2026-10-22T10:05:00", "code": "worker_capacity",
                                                     "reason": "This worker pool is busy"})
    service.db.set_setting("owner_apply:" + first, {"at": "2026-10-22T10:00:00", "code": "ats_pacing",
                                                    "reason": "1 icims application already queued"})
    view = client.get("/api/queue").json()
    assert [a["job_id"] for a in view["active"]] == [running]
    assert [w["job_id"] for w in view["waiting"]] == [first, second]
    assert view["waiting"][0]["code"] == "ats_pacing" and view["waiting"][0]["title"] == "Platform Engineer 2"
    assert client.delete(f"/api/queue/{first}").json() == {"removed": True, "job_id": first}
    assert [w["job_id"] for w in client.get("/api/queue").json()["waiting"]] == [second]
    assert client.delete(f"/api/queue/{first}").status_code == 404
