"""The success number an investor can check: every exclusion has a structured reason, every confirmation a grade of evidence.

Synthetic rows. Each case pairs a job that is left out or graded down with its closest look-alike that is not, so a loosened
rule names the kind it broke. The same rules run as SQL in scripts/metrics/success.sql (check_success.py compares them)."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import insert

from jobpilot.api.success import success
from jobpilot.api.success_core import evidence
from jobpilot.db import Event, Job, Run, Setting

pytestmark = pytest.mark.integration

NOW = datetime(2026, 11, 2, 12, tzinfo=UTC)
PAGE = {"type": "explicit_confirmation_page", "website_confirmed": True}


def at(days_ago):
    return (NOW - timedelta(days=days_ago)).isoformat()


def seed(service, jobs, events=(), settings=()):
    """jobs: {name: [(state, days_ago, blocker or None, receipt or None, reason)]}; ids are the names, runs are name-0, name-1."""
    with service.db.session() as s:
        s.execute(insert(Job), [dict(id=n, identity=f"example.test/{n}", url=f"https://boards.example.test/{n}", title="Engineer",
                                     company="Example Co", location="Remote", ats="greenhouse", status="ready", demo=False,
                                     created_at=at(30)) for n in jobs])
        s.execute(insert(Run), [
            dict(id=f"{n}-{i}", job_id=n, state=state, mode="submit", reason=reason or "", packet={"blocker_type": blocker} if blocker else {},
                 receipt=receipt or {}, created_at=at(days))
            for n, runs in jobs.items() for i, (state, days, blocker, receipt, reason) in enumerate(runs)
        ])
        if events:
            s.execute(insert(Event), [dict(run_id=r, kind=k, message="", data={}, created_at=at(1)) for r, k in events])
        if settings:
            s.execute(insert(Setting), [dict(key=k, value=v) for k, v in settings])


def overall(service):
    with service.db.session() as s:
        return success(s, 7, now=NOW)["overall"]


def run(state, days=1, blocker=None, receipt=None, reason=""):
    return (state, days, blocker, receipt, reason)


def test_each_exclusion_is_a_structured_code_and_the_rest_are_failures(service):
    seed(service, {
        "limit": [run("closed", blocker="employer_limit")],
        "ssn": [run("cancelled", blocker="sensitive_id")],
        "gone": [run("closed")],
        "withdrawn": [run("cancelled")],
        "paused": [run("paused")],
        "asks": [run("waiting_answer")],
        "fact": [run("waiting_browser", blocker="missing_fact")],
        "going": [run("running")],
        "twice": [run("already_applied")],
        "stuck": [run("waiting_browser", blocker="control_failure")],
        "email": [run("cancelled", blocker="email_wait")],  # a cancelled run that names a blocker is a failure, not a withdrawal
        "unknown": [run("submission_unknown")],
        "yes": [run("confirmed", receipt=PAGE)],
    })
    t = overall(service)
    assert t["excluded"] == {
        "already_on_file": 1, "in_progress": 1, "employer_limit": 1, "sensitive_id_required": 1, "closed": 1,
        "cancelled": 1, "paused_by_owner": 1, "waiting_on_owner": 2,
    }
    assert t["failures"] == {"waiting_browser:control_failure": 1, "cancelled:email_wait": 1, "submission_unknown:unclassified": 1}
    assert t["attempted"] == 13 and t["confirmed"] == 1 and t["failed"] == 3 and t["eligible"] == 4
    assert t["raw_pct"] == 7.6 and t["final_pct"] == 25.0 and t["first_try_pct"] == 25.0
    # Counting the jobs that wait on the owner or are still running as misses: 1 of 4 + 2 + 1 = 7
    assert t["strict_pct"] == 14.2


def test_the_words_of_a_failure_decide_nothing(service):
    """The first report read the last reason for "supervisor", "reference", "video"... A failed run that mentions them is still a failure."""
    seed(service, {
        "mentions": [run("waiting_browser", reason="Could not click Next after the supervisor reference section")],
        "asks": [run("waiting_answer", reason="Needs a supervisor reference")],
    })
    t = overall(service)
    assert t["failed"] == 1 and t["excluded"]["waiting_on_owner"] == 1


def test_a_job_the_owner_deleted_after_a_failure_is_still_a_failure(service):
    seed(service, {"deleted": [run("waiting_browser", blocker="control_failure")]},
         settings=[("job_control:deleted", {"state": "deleted"})])
    t = overall(service)
    assert t["failed"] == 1 and t["eligible"] == 1 and t["final_pct"] == 0.0


def test_evidence_is_graded_and_reported_beside_the_rate(service):
    email = {"message_id": "m1", "sender_trusted": True, "authenticated": True}
    seed(service, {
        "page": [run("confirmed", receipt=PAGE)],
        "both": [run("confirmed", receipt={**PAGE, "email": email})],
        "mail": [run("confirmed", receipt={"email": email})],
        "created": [run("confirmed", receipt={"type": "application_created"})],
        "said": [run("confirmed", receipt={"type": "user_reconciled", "evidence": "the applicant says so"})],
    }, events=[("said-0", "submitting")])
    t = overall(service)
    assert t["confirmed"] == 5 and t["final_pct"] == 100.0
    assert t["evidence"]["strong"] == 3 and t["evidence"]["medium"] == 1 and t["evidence"]["weak"] == 1
    assert t["evidence"]["kinds"] == {"page_only": 1, "page_and_email": 1, "email_only": 1, "platform_response": 1, "applicant_recorded": 1}
    assert t["strong_pct"] == 60.0  # 3 strong of 5 eligible: a rate the weak and medium receipts do not lift


def test_a_weak_receipt_with_no_commit_of_ours_is_someone_elses_application(service):
    """Its look-alike, with a commit of AXIOM's first, counts. A bare confirmed run (no receipt at all) is graded weak but stays."""
    quote = {"type": "final_page_confirmation", "evidence": "the applied-application view"}
    seed(service, {
        "theirs": [run("confirmed", receipt=quote)],
        "ours": [run("submission_unknown", days=3), run("confirmed", receipt=quote)],
        "bare": [run("confirmed")],
    }, events=[("ours-0", "submitting")])
    t = overall(service)
    assert t["excluded"]["already_on_file"] == 1 and t["confirmed"] == 2
    assert t["evidence"]["kinds"] == {"final_page_quote": 1, "unrecognised": 1}


def test_a_repair_agents_page_that_is_only_a_candidate_home_heading_is_weak(service):
    assert evidence({"type": "invalid_page_text"}) == ("weak", "page_text_rejected")
    assert evidence({"type": "explicit_confirmation_page", "web": "true", "sel": "[data-automation-id=similar-jobs-heading]"})[0] == "weak"
    assert evidence({"type": "explicit_confirmation_page", "web": "true"}) == ("strong", "page_only")
    # an untrusted sender's mail is no proof; a page-less receipt that names only such mail is weak
    assert evidence({"mail": "m1", "trusted": "false"}) == ("weak", "email_untrusted_sender")


def test_the_cohort_is_the_jobs_first_tried_in_the_window(service):
    """A job first tried 10 days ago and confirmed on a retry yesterday belongs to the earlier week: its retry is not a first try."""
    seed(service, {
        "early": [run("failed", days=10), run("confirmed", days=1, receipt=PAGE)],
        "fresh": [run("failed", days=3), run("confirmed", days=1, receipt=PAGE)],
        "firsttry": [run("confirmed", days=2, receipt=PAGE)],
    })
    t = overall(service)
    assert t["attempted"] == 2 and t["confirmed"] == 2
    assert t["first_try_pct"] == 50.0  # only "firsttry" confirmed on its first run
