"""HTTP contract of the ledger of confirmed applications and of the archive of failed ones."""

import gzip
import json

from support_http import seed_job, seed_run


def test_the_ledger_lists_each_confirmed_job_once_and_skips_pending_evidence(client, service):
    one, two, three = seed_job(service, 1), seed_job(service, 2), seed_job(service, 3)
    seed_run(service, one, state="confirmed", receipt={"website_confirmed": True})
    seed_run(
        service, one, state="confirmed", receipt={"website_confirmed": True}
    )  # a second run of the same job
    seed_run(service, two, state="confirmed", receipt={"evidence_reconciliation": "pending"})
    seed_run(service, three, state="failed")
    seed_run(service, seed_job(service, 4, demo=True), state="confirmed")
    rows = client.get("/api/successful-applications").json()["applications"]
    assert [r["job_id"] for r in rows] == [one]
    assert set(rows[0]) == {
        "job_id", "run_id", "title", "company", "url", "applied_at", "ats",
        "website_confirmed", "email_confirmed", "existing_application",
    }  # fmt: skip
    assert rows[0]["website_confirmed"] is True and rows[0]["email_confirmed"] is False


def test_the_archive_starts_empty(client):
    assert client.get("/api/archive").json() == {"applications": [], "log_bytes": 0}


def test_archiving_failed_applications_moves_them_to_the_archive_with_a_log(client, service):
    failed, confirmed = seed_job(service, 1), seed_job(service, 2)
    seed_run(service, failed, state="failed", reason="Employer closed the posting")
    seed_run(service, confirmed, state="confirmed")
    result = client.post("/api/archive/failed", json={}).json()
    assert result["archived"] == 1 and result["log_bytes"] > 0
    listed = client.get("/api/archive").json()
    assert [a["job_id"] for a in listed["applications"]] == [failed]
    assert listed["log_bytes"] == result["log_bytes"]
    log = client.get(f"/api/archive/{failed}/log")
    assert log.status_code == 200 and log.headers["content-type"] == "application/gzip"
    lines = [json.loads(line) for line in gzip.decompress(log.content).decode().splitlines()]
    assert lines[0]["run"]["state"] == "failed"


def test_the_log_of_a_job_that_was_never_archived_is_a_400(client, service):
    assert client.get(f"/api/archive/{seed_job(service)}/log").status_code == 400
