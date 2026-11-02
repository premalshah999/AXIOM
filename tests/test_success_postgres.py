"""The success number on a real PostgreSQL, the database every installation runs: `json` columns, the real driver, the real route.

The other success tests run on SQLite. Here the route and scripts/metrics/success.sql count the same rows, line by line, one job of
every kind each rule can see, so a rule changed in one and not the other names itself. Synthetic rows; a computer without PostgreSQL
17 skips the file.
"""

import importlib.util
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import insert, text

from jobpilot.db import Database, Event, Job, Run
from jobpilot.features._store import create_tables
from jobpilot.host_health import INTERRUPTED
from jobpilot.service import Service

from support_pg import USER, ScratchServer, find_bin

pytestmark = pytest.mark.integration

REPO = Path(__file__).resolve().parents[1]
SQL = REPO / "scripts/metrics/success.sql"
_spec = importlib.util.spec_from_file_location("check_success", REPO / "scripts/metrics/check_success.py")
check = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check)

MAIL = {"message_id": "m1", "sender_trusted": True, "authenticated": True}
PAGE = {"type": "explicit_confirmation_page", "website_confirmed": True}
NOTICE = {"existing_application_notice": True, "reconciled_existing_submission": True}
MATCHED = {"matched_existing_application": True, "email": MAIL, "email_confirmed": True, "evidence_reconciliation": "verified"}
QUOTE = {"type": "final_page_confirmation", "evidence": "Thank you"}

# name: (platform, [(state, days ago, blocker, receipt, reason)], commits on these run numbers)
JOBS = {
    # Every grade of evidence.
    "page": ("greenhouse", [("confirmed", 1, None, PAGE, "")], [0]),
    "page_flag_false": ("greenhouse", [("confirmed", 1, None, {**PAGE, "existing_application_notice": False}, "")], [0]),
    "page_mail": ("greenhouse", [("confirmed", 1, None, {**PAGE, "email": MAIL}, "")], [0]),
    "mail": ("greenhouse", [("confirmed", 1, None, {"email": MAIL}, "")], []),
    "mail_untrusted": ("greenhouse", [("confirmed", 1, None, {"email": {**MAIL, "sender_trusted": False}}, "")], []),
    "created": ("ashby", [("confirmed", 1, None, {"type": "application_created"}, "")], []),
    "created_mail": ("ashby", [("confirmed", 1, None, {"type": "application_created", "email": MAIL}, "")], []),
    "unknown_type": ("ashby", [("confirmed", 1, None, {"type": "something_new"}, "")], []),
    "bare": ("ashby", [("confirmed", 1, None, {}, "")], []),
    # A receipt that only speaks of an application: ours with a commit first, someone else's without.
    "notice_ours": ("workday", [("submission_unknown", 3, None, {}, ""), ("confirmed", 1, None, NOTICE, "")], [0]),
    "notice_theirs": ("workday", [("confirmed", 1, None, NOTICE, "")], []),
    "commit_after": ("workday", [("confirmed", 3, None, NOTICE, ""), ("confirmed", 1, None, PAGE, "")], [1]),
    "matched_ours": ("workday", [("submission_unknown", 3, None, {}, ""), ("confirmed", 1, None, MATCHED, "")], [0]),
    "matched_theirs": ("workday", [("failed", 3, None, {}, ""), ("confirmed", 1, None, MATCHED, "")], []),
    "refused": ("workday", [("confirmed", 1, None, {"type": "employer_already_applied"}, "")], []),
    "quote_ours": ("workday", [("confirmed", 1, None, QUOTE, "")], [0]),
    "quote_theirs": ("workday", [("confirmed", 1, None, QUOTE, "")], []),
    "noted": ("workday", [("confirmed", 1, None, {"type": "user_reconciled"}, "")], [0]),
    "dashboard": ("icims", [("confirmed", 1, None, {"type": "employer_dashboard_status"}, "")], [0]),
    "rejected": ("icims", [("confirmed", 1, None, {"type": "invalid_page_text"}, "")], [0]),
    "heading": ("icims", [("confirmed", 1, None, {**PAGE, "evidence_selector": "[data-automation-id=similar-jobs-heading]"}, "")], [0]),
    "pending": ("icims", [("confirmed", 1, None, {**PAGE, "evidence_reconciliation": "pending"}, "")], [0]),
    # Each reason a job is left out, each kind of failure.
    "limit": ("lever", [("closed", 1, "employer_limit", {}, "")], []),
    "ssn": ("lever", [("cancelled", 1, "sensitive_id", {}, "")], []),
    "gone": ("lever", [("closed", 1, None, {}, "")], []),
    "withdrawn": ("lever", [("cancelled", 1, None, {}, "")], []),
    "paused": ("lever", [("paused", 1, None, {}, "")], []),
    "asks": ("lever", [("waiting_answer", 1, None, {}, "")], []),
    "fact": ("lever", [("waiting_browser", 1, "missing_fact", {}, "")], []),
    "twice": ("lever", [("already_applied", 1, None, {}, "")], []),
    "running": ("lever", [("running", 1, None, {}, "")], []),
    "queued": ("lever", [("queued", 1, None, {}, "")], []),
    "mailing": ("lever", [("waiting_email", 1, None, {}, "")], []),
    "stuck": ("oracle", [("waiting_browser", 1, "control_failure", {}, "")], []),
    "cancel_blocked": ("oracle", [("cancelled", 1, "email_wait", {}, "")], []),
    "unsure": ("oracle", [("submission_unknown", 1, None, {}, "")], []),
    "review": ("oracle", [("needs_review", 1, "validation", {}, "")], []),
    "timeout": ("oracle", [("timed_out", 1, "email_wait", {}, "")], []),
    # What first means, and what the window takes.
    "second": ("oracle", [("failed", 3, None, {}, ""), ("confirmed", 1, None, PAGE, "")], []),
    "resumed": ("oracle", [("waiting_browser", 3, None, {}, INTERRUPTED), ("confirmed", 1, None, PAGE, "")], []),
    "only_cut": ("oracle", [("waiting_browser", 1, None, {}, INTERRUPTED)], []),
    "dry_first": ("oracle", [("dry_run_passed", 3, None, {}, ""), ("failed", 1, None, {}, "")], []),
    "dry_last": ("oracle", [("failed", 3, None, {}, ""), ("dry_run_passed", 1, None, {}, "")], []),
    "dry_only": ("oracle", [("dry_run_passed", 1, None, {}, "")], []),
    "then_closed": ("oracle", [("confirmed", 3, None, PAGE, ""), ("closed", 1, None, {}, "")], []),
    "first_tried_earlier": ("oracle", [("failed", 20, None, {}, ""), ("confirmed", 1, None, PAGE, "")], []),
    "outside": ("oracle", [("confirmed", 20, None, PAGE, "")], []),
    "only_excluded": ("rippling", [("closed", 1, None, {}, "")], []),
}


@pytest.fixture(scope="session")
def scratch_server(tmp_path_factory):
    bin_dir = find_bin()
    if bin_dir is None:
        pytest.skip("PostgreSQL 17 is not installed (brew install postgresql@17)")
    server = ScratchServer(bin_dir, tmp_path_factory.mktemp("success-pg") / "data").start()
    yield server
    server.stop()


@pytest.fixture
def database(scratch_server):
    return scratch_server.create_database()


@pytest.fixture
def service(config, scratch_server, database):
    """The Service on a fresh database of the scratch server (the conftest fixture of the same name uses SQLite)."""
    config.database_url = scratch_server.url(database)
    db = Database(config.database_url)
    created = Service(db, config)
    create_tables(db.engine)
    yield created
    db.engine.dispose()


def seed(service, jobs=JOBS):
    now = datetime.now(UTC)
    stamp = lambda days: (now - timedelta(days=days)).isoformat()  # noqa: E731
    with service.db.session() as s:
        s.execute(insert(Job), [
            dict(id=n, identity=f"example.test/{n}", url=f"https://boards.example.test/{n}", title="Engineer", company="Example Co",
                 location="Remote", ats=ats, status="ready", demo=False, created_at=stamp(30))
            for n, (ats, _, _) in jobs.items()
        ])
        s.execute(insert(Run), [
            dict(id=f"{n}-{i}", job_id=n, state=state, mode="submit", reason=reason, packet={"blocker_type": blocker} if blocker else {},
                 receipt=receipt, created_at=stamp(days), finished_at=stamp(days))
            for n, (_, runs, _) in jobs.items()
            for i, (state, days, blocker, receipt, reason) in enumerate(runs)
        ])
        commits = [dict(run_id=f"{n}-{i}", kind="submitting", message="", data={}, created_at=stamp(1))
                   for n, (_, _, on) in jobs.items() for i in on]
        if commits:
            s.execute(insert(Event), commits)
        # Detail events a run may or may not still have: none of them is a commit.
        s.execute(insert(Event), [dict(run_id="quote_theirs-0", kind=k, message="", data={}, created_at=stamp(1))
                                  for k in ("step_submit", "submission_requests", "submission", "form_post")])


def run_sql(scratch_server, database, cutoff):
    done = subprocess.run(
        [str(scratch_server.bin / "psql"), "-XAt", "-F", "|", "-h", "127.0.0.1", "-p", str(scratch_server.port), "-U", USER,
         "-d", database, "-v", "ON_ERROR_STOP=1", "-v", f"cutoff={cutoff}", "-f", str(SQL)],
        capture_output=True, text=True, timeout=60,
    )
    assert done.returncode == 0, done.stderr
    return done.stdout.splitlines()


def test_these_columns_are_what_an_installation_has(service):
    """`json`, not `jsonb`: reading one field parses the document, which is why a packet is read only where it decides."""
    with service.db.session() as s:
        types = dict(s.execute(text(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_name = 'runs' AND column_name IN ('packet', 'receipt')"
        )).all())
    assert types == {"packet": "json", "receipt": "json"}


def test_the_route_and_the_query_agree_on_every_kind_of_job(client, service, scratch_server, database):
    seed(service)
    reply = client.get("/api/success")
    assert reply.status_code == 200
    report = reply.json()
    t = report["overall"]
    # Read by hand from JOBS, so a rule both sides got wrong the same way still fails.
    assert t["excluded"] == {
        "already_on_file": 5, "in_progress": 4, "employer_limit": 1, "sensitive_id_required": 1, "closed": 2, "cancelled": 1,
        "paused_by_owner": 1, "waiting_on_owner": 2,
    }
    assert t["failures"] == {
        "waiting_browser:control_failure": 1, "cancelled:email_wait": 1, "submission_unknown:unclassified": 2,
        "needs_review:validation": 1, "timed_out:email_wait": 1, "failed:unclassified": 1, "dry_run_passed:unclassified": 1,
    }
    assert t["evidence"]["kinds"] == {
        "page_only": 6, "page_and_email": 1, "email_only": 1, "email_untrusted_sender": 1, "platform_response": 1, "platform_and_email": 1,
        "unrecognised": 2, "existing_application_view": 2, "final_page_quote": 1, "applicant_recorded": 1, "dashboard_status": 1,
        "page_text_rejected": 2,
    }
    assert t["attempted"] == t["eligible"] + sum(t["excluded"].values()) and t["eligible"] == t["confirmed"] + t["failed"] == 28
    assert t["attempted"] == 45 and t["confirmed"] == 20 and t["first_try"] == 16 and t["evidence"]["strong"] == 9 and t["strong_pct"] == 32.1
    assert check.differences(check.figures(report), check.sql_figures(run_sql(scratch_server, database, report["since"]))) == []


def test_an_empty_database_agrees_too(client, scratch_server, database):
    report = client.get("/api/success").json()
    assert report["overall"]["tried"] == 0 and report["overall"]["final_pct"] is None
    assert check.differences(check.figures(report), check.sql_figures(run_sql(scratch_server, database, report["since"]))) == []


def test_the_list_holds_what_the_score_confirmed_and_names_the_rest(client, service):
    seed(service)
    listed = client.get("/api/successful-applications").json()["applications"]
    jobs = {r["job_id"] for r in listed}
    theirs = {r["job_id"] for r in listed if r["existing_application"]}
    assert theirs == {"notice_theirs", "matched_theirs", "refused", "quote_theirs"}  # "twice" is no confirmation at all
    assert "pending" not in jobs
    report = client.get("/api/success").json()["overall"]
    # The 7 days' sent applications on the list are the score's confirmed jobs, and the ones first tried before those 7 days.
    since = datetime.now(UTC) - timedelta(days=7)
    sent = [r for r in listed if not r["existing_application"] and datetime.fromisoformat(r["applied_at"]) >= since]
    assert len(sent) == report["confirmed"] + 1 and "first_tried_earlier" in {r["job_id"] for r in sent}
    assert "outside" in jobs and "outside" not in {r["job_id"] for r in sent}
