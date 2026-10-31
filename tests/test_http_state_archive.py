"""HTTP contract of the ledger of confirmed applications and of the archive of failed ones."""

import csv
import gzip
import io
import json
from pathlib import Path

from jobpilot.api.routes.state import PROOF_LABELS
from jobpilot.db import Job
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
        "website_confirmed", "email_confirmed", "existing_application", "proof",
    }  # fmt: skip
    assert rows[0]["website_confirmed"] is True and rows[0]["email_confirmed"] is False
    assert rows[0]["proof"] == "page"


def test_each_confirmed_application_says_what_proved_it(client, service):
    receipts = {
        "page": {"website_confirmed": True, "type": "explicit_confirmation_page"},
        "both": {"website_confirmed": True, "email": {"sender_trusted": True}},
        "email": {"email": {"sender_trusted": True, "authenticated": True}},  # a receipt with no type but a trusted email
        "existing": {"existing_application_notice": "You already applied"},
        "platform": {"type": "application_created", "status": 201},
        "applicant": {"type": "user_reconciled", "evidence": "Checked the employer's mail"},
        "final_page": {"type": "final_page_confirmation"},
        "dashboard": {"type": "employer_dashboard_status", "status": "Received"},
        "none": {"type": "invalid_page_text"},  # the page text was not a confirmation and nothing else vouches for it
    }
    jobs = {proof: seed_job(service, n) for n, proof in enumerate(receipts, 1)}
    for proof, receipt in receipts.items():
        seed_run(service, jobs[proof], state="confirmed", receipt=receipt)
    by_job = {r["job_id"]: r["proof"] for r in client.get("/api/successful-applications").json()["applications"]}
    assert {proof: by_job[job] for proof, job in jobs.items()} == {proof: proof for proof in receipts}
    # The page and the spreadsheet use the same words: the page's table is in model.ts.
    model = (Path(__file__).parents[1] / "frontend/src/model.ts").read_text()
    assert all(f'"{label}"' in model for label in PROOF_LABELS.values())


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


def test_the_ledger_exports_as_a_spreadsheet_the_page_can_filter(client, service):
    one, two, three = seed_job(service, 1, ats="workday"), seed_job(service, 2), seed_job(service, 3)
    seed_run(service, one, state="confirmed", receipt={"website_confirmed": True, "email_confirmed": True})
    seed_run(service, two, state="confirmed", receipt={"existing_application_notice": "You already applied"})
    seed_run(service, three, state="confirmed")  # nothing on file at all
    with service.db.exclusive() as s:
        s.get(Job, one).company = "=cmd|' /C calc'!A0"  # a formula must not survive into a spreadsheet
        s.get(Job, two).company = ""  # saved without an employer: the page shows one taken from its link, so search finds it there
    reply = client.get("/api/successful-applications.csv")
    assert reply.status_code == 200 and reply.headers["content-type"].startswith("text/csv")
    assert reply.headers["content-disposition"] == 'attachment; filename="AXIOM-confirmed-applications.csv"'
    rows = list(csv.reader(io.StringIO(reply.text.removeprefix("﻿"))))
    assert rows[0] == ["Company", "Job title", "Sent (UTC)", "Posting link", "Platform", "Proof"]
    assert {row[5] for row in rows[1:]} == {"Confirmation page and email", "Existing application", "No receipt on file"}
    assert "'=cmd" in reply.text and ',"=cmd' not in reply.text

    def got(**query):
        text = client.get("/api/successful-applications.csv", params=query).text.removeprefix("\ufeff")
        return sorted(row[1] for row in list(csv.reader(io.StringIO(text)))[1:])

    assert got(evidence="email") == ["Platform Engineer 1"]
    assert got(evidence="none") == ["Platform Engineer 3"]
    assert got(evidence="page") == ["Platform Engineer 1"]  # the page filter takes a job proved by both
    assert got(ats="workday") == ["Platform Engineer 1"]
    assert got(q="engineer 2") == ["Platform Engineer 2"]
    assert got(q="example.test") == ["Platform Engineer 2"]
    assert len(got(since="2000-01-01T00:00:00Z")) == 2  # sent by AXIOM: the employer's own notice is not
    assert got(since="2999-01-01T00:00:00Z") == []
