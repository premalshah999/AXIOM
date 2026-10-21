"""HTTP contract of the job routes: import, detail, apply, queue, pause, resume and remove."""

from jobpilot.db import Job, Setting, Run
from sqlalchemy import select
from support_http import seed_job, seed_run

URL = "https://boards.example.test/acme/jobs/4242"


def test_importing_a_job_saves_it_once(client, service):
    first = client.post("/api/jobs", json={"url": URL, "title": "Data Engineer", "company": "Acme Example"})
    assert first.status_code == 200
    body = first.json()
    assert body["created"] is True and body["job"]["title"] == "Data Engineer"
    again = client.post("/api/jobs", json={"url": URL}).json()
    assert again["created"] is False and again["job"]["id"] == body["job"]["id"]


def test_importing_a_url_that_is_not_http_is_a_400(client):
    answer = client.post("/api/jobs", json={"url": "ftp://boards.example.test/acme/jobs/1"})
    assert answer.status_code == 400


def test_job_detail_has_the_job_its_latest_run_and_its_control_state(client, service):
    job_id = seed_job(service)
    older = seed_run(service, job_id, state="failed")
    newer = seed_run(service, job_id, state="queued")
    body = client.get(f"/api/jobs/{job_id}").json()
    assert set(body) == {"job", "latest_run", "job_control"}
    assert body["job"]["id"] == job_id and "description" not in body["job"]
    assert body["latest_run"]["id"] in {older, newer}
    assert "packet" not in body["latest_run"] and "receipt" not in body["latest_run"]
    assert body["job_control"] == {}


def test_job_detail_of_an_unknown_id_is_404(client):
    answer = client.get("/api/jobs/unknown")
    assert answer.status_code == 404 and answer.json() == {"detail": "Job not found"}


def test_apply_and_queue_refuse_when_workers_are_disabled(client, service):
    job_id = seed_job(service, status="ready")
    for call in (
        client.post(f"/api/jobs/{job_id}/apply"),
        client.post(f"/api/jobs/{job_id}/queue", json={"mode": "dry_run"}),
    ):
        assert call.status_code == 400 and call.json() == {"detail": "Workers are disabled"}


def test_queue_rejects_an_unknown_mode_as_a_schema_error(client, service):
    job_id = seed_job(service)
    assert client.post(f"/api/jobs/{job_id}/queue", json={"mode": "everything"}).status_code == 422


def test_pause_cancels_queued_runs_and_marks_the_job_paused(client, service):
    job_id = seed_job(service, status="ready")
    run_id = seed_run(service, job_id, state="queued")
    answer = client.post(f"/api/jobs/{job_id}/pause")
    assert answer.status_code == 200 and answer.json()["job_id"] == job_id
    with service.db.session() as s:
        assert s.get(Run, run_id).state == "paused"
        assert s.get(Job, job_id).status == "paused"
    assert client.get(f"/api/jobs/{job_id}").json()["job_control"]["state"] == "paused"


def test_remove_archives_the_job_and_keeps_its_history(client, service):
    job_id = seed_job(service, status="ready")
    seed_run(service, job_id, state="failed", reason="Employer closed the posting")
    answer = client.delete(f"/api/jobs/{job_id}")
    assert answer.status_code == 200
    with service.db.session() as s:
        assert s.get(Job, job_id).status == "archived"
        assert s.scalars(select(Run).where(Run.job_id == job_id)).first() is not None
        assert s.get(Setting, "job_control:" + job_id).value["state"] == "deleted"
    # A removed job can no longer be resumed.
    resumed = client.post(f"/api/jobs/{job_id}/resume")
    assert resumed.status_code == 400 and "removed" in resumed.json()["detail"]


def test_pause_of_an_unknown_job_is_a_400(client):
    assert client.post("/api/jobs/unknown/pause").status_code == 400
