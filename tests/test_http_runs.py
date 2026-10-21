"""HTTP contract of the run routes: detail, screenshot, reconcile, cancel, verify and answering a review."""

from jobpilot.db import Event, Job, Run
from support_http import seed_job, seed_run


def seed_event(service, run_id):
    with service.db.exclusive() as s:
        s.add(Event(run_id=run_id, kind="step", message="Opened the form", data={}))


def test_run_detail_has_the_run_its_events_and_the_failure_report(client, service, config):
    job_id = seed_job(service)
    run_id = seed_run(service, job_id, state="failed", reason="Employer closed the posting")
    seed_event(service, run_id)
    service.db.set_setting("failure_report:" + run_id, {"summary": "Posting closed"})
    folder = config.data_dir / "runs" / run_id
    folder.mkdir(parents=True)
    (folder / "answers.json").write_text('{"first_name": "Alex"}')
    (folder / "final.png").write_bytes(b"\x89PNG\r\n")
    body = client.get(f"/api/runs/{run_id}").json()
    assert set(body) == {"run", "events", "answers", "screenshot", "demo_receipt", "failure_report"}
    assert body["run"]["id"] == run_id and "packet" not in body["run"]
    assert [e["message"] for e in body["events"]] == ["Opened the form"]
    assert body["answers"] == {"first_name": "Alex"}
    assert body["screenshot"] is True and body["demo_receipt"] is None
    assert body["failure_report"] == {"summary": "Posting closed"}


def test_run_detail_of_an_unknown_run_is_404(client):
    assert client.get("/api/runs/unknown").status_code == 404
    assert client.get("/api/runs/unknown/screenshot").status_code == 404


def test_screenshot_is_the_saved_png_or_404(client, service, config):
    run_id = seed_run(service, seed_job(service), state="failed")
    assert client.get(f"/api/runs/{run_id}/screenshot").status_code == 404
    folder = config.data_dir / "runs" / run_id
    folder.mkdir(parents=True)
    (folder / "final.png").write_bytes(b"\x89PNG\r\n")
    answer = client.get(f"/api/runs/{run_id}/screenshot")
    assert answer.status_code == 200 and answer.headers["content-type"] == "image/png"


def test_only_a_queued_run_can_be_cancelled(client, service):
    job_id = seed_job(service)
    queued = seed_run(service, job_id, state="queued")
    running = seed_run(service, seed_job(service, 2), state="running")
    assert client.post(f"/api/runs/{running}/cancel").status_code == 400
    assert client.post(f"/api/runs/{queued}/cancel").json() == {"cancelled": True}
    with service.db.session() as s:
        assert s.get(Run, queued).state == "cancelled"
        assert s.get(Job, job_id).status == "cancelled"


def test_reconcile_needs_evidence_of_at_least_five_characters(client, service):
    run_id = seed_run(service, seed_job(service), state="submission_unknown")
    assert (
        client.post(f"/api/runs/{run_id}/reconcile", json={"submitted": False, "evidence": "no"}).status_code
        == 422
    )


def test_reconcile_records_that_the_application_was_not_submitted(client, service):
    run_id = seed_run(service, seed_job(service), state="submission_unknown")
    answer = client.post(
        f"/api/runs/{run_id}/reconcile",
        json={"submitted": False, "evidence": "The employer portal shows no application"},
    )
    assert answer.status_code == 200 and answer.json() == {"reconciled": True}
    with service.db.session() as s:
        assert s.get(Run, run_id).state != "submission_unknown"


def test_verify_without_a_retained_draft_is_404_or_409(client, service):
    assert client.post("/api/runs/unknown/verify").status_code == 404
    run_id = seed_run(service, seed_job(service), state="failed")
    assert client.post(f"/api/runs/{run_id}/verify").status_code in {404, 409}


def test_an_answer_must_not_be_empty(client):
    assert client.post("/api/reviews/any", json={"answer": ""}).status_code == 422


def test_answering_an_unknown_review_is_a_client_error(client):
    answer = client.post("/api/reviews/unknown", json={"answer": "Yes"})
    assert 400 <= answer.status_code < 500


def test_the_receipt_check_for_an_unknown_run_is_not_a_server_error(client):
    assert client.post("/api/runs/unknown/check-email").status_code < 500
