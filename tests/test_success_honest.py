"""The success number an investor can check: every exclusion has a structured reason, every confirmation a grade of evidence.

Synthetic rows. Each case pairs a job that is left out or graded down with its closest look-alike that is not, so a loosened
rule names the kind it broke. The same rules run as SQL in scripts/metrics/success.sql (check_success.py compares them)."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import event, insert

from jobpilot.api.success import success
from jobpilot.api.success_core import evidence
from jobpilot.db import Event, Job, Run, Setting
from jobpilot.host_health import INTERRUPTED

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


def test_only_the_commit_event_makes_a_weak_receipt_ours(service):
    """The event the engine writes before its submit click, and keeps. Detail events go with a run's logs after a week, so the same
    job would change from ours to someone else's as its logs aged; and an iCIMS step_submit is a step before the last."""
    quote = {"type": "final_page_confirmation", "evidence": "Thank you"}
    seed(service, {
        "clicked": [run("submission_unknown", days=3), run("confirmed", receipt=quote)],
        "step": [run("submission_unknown", days=3), run("confirmed", receipt=quote)],
        "requests": [run("submission_unknown", days=3), run("confirmed", receipt=quote)],
        "acknowledged": [run("submission_unknown", days=3), run("confirmed", receipt=quote)],
        "posted": [run("submission_unknown", days=3), run("confirmed", receipt=quote)],
    }, events=[("clicked-0", "submitting"), ("step-0", "step_submit"), ("requests-0", "submission_requests"),
               ("acknowledged-0", "submission"), ("posted-0", "form_post")])
    t = overall(service)
    assert t["confirmed"] == 1 and t["excluded"]["already_on_file"] == 4


def test_a_mail_matched_to_an_existing_application_needs_our_commit_like_the_employers_page(service):
    """One definition: every receipt that speaks of an application already on file, however it reached the run."""
    mail = {"message_id": "m1", "sender_trusted": True, "authenticated": True}
    matched = {"matched_existing_application": True, "email": mail, "email_confirmed": True, "evidence_reconciliation": "verified"}
    seed(service, {
        "theirs": [run("confirmed", receipt=matched)],
        "ours": [run("submission_unknown", days=3), run("confirmed", receipt=matched)],
        "refused": [run("confirmed", receipt={"type": "employer_already_applied"})],
    }, events=[("ours-0", "submitting")])
    t = overall(service)
    assert t["confirmed"] == 1 and t["excluded"]["already_on_file"] == 2 and t["evidence"]["kinds"] == {"existing_application_view": 1}


def test_a_confirmation_still_being_reconciled_is_not_one_yet(service):
    """pipeline.settled_confirmation: what the ledger, the Jobs chip and the batch goal all count. Without this the score could
    say more than the list it links to."""
    seed(service, {
        "checking": [run("confirmed", receipt={**PAGE, "evidence_reconciliation": "pending"})],
        "checked": [run("confirmed", receipt={**PAGE, "evidence_reconciliation": "verified"})],
    })
    t = overall(service)
    assert t["confirmed"] == 1 and t["failed"] == 1 and t["failures"] == {"submission_unknown:unclassified": 1}
    assert t["first_try"] == 1 and t["evidence"]["strong"] == 1


def statements(service):
    seen = []

    def record(conn, cursor, statement, parameters, context, executemany):
        seen.append((" ".join(statement.split()), parameters))

    event.listen(service.db.engine, "before_cursor_execute", record)
    return seen, lambda: event.remove(service.db.engine, "before_cursor_execute", record)


def test_only_the_last_run_of_an_unconfirmed_job_has_its_packet_read(service):
    """Overview polls this every minute, and a packet is a large JSON document: reading a blocker from every run of the window took
    7.7 s of the production database's time for 9,997 runs against 0.2 s without (2026-11-02), as on 2026-10-29."""
    seed(service, {
        "yes": [run("confirmed", receipt=PAGE)],
        "going": [run("running")],
        "cut": [run("waiting_browser", reason=INTERRUPTED)],
        "lost": [run("failed", days=3), run("waiting_browser", blocker="control_failure")],
        "ended": [run("waiting_browser", days=3, blocker="captcha"), run("closed")],
    })
    seen, stop = statements(service)
    try:
        t = overall(service)
    finally:
        stop()
    assert t["failures"] == {"waiting_browser:control_failure": 1} and t["excluded"]["closed"] == 1
    reading = [(sql, parameters) for sql, parameters in seen if "packet" in sql]
    assert len(reading) == 1
    assert {"lost-1", "ended-1"} == {p for p in reading[0][1] if isinstance(p, str) and p.endswith(("-0", "-1"))}


def ledger_row(client, job):
    return next(r for r in client.get("/api/successful-applications").json()["applications"] if r["job_id"] == job)


def test_the_list_and_the_score_ask_one_question_of_who_sent_it(client, service):
    """Applications' "sent" filters and Overview's "Confirmed today" read `existing_application`; the score reads the same test."""
    now = datetime.now(UTC)
    quote = {"type": "final_page_confirmation", "evidence": "Thank you"}
    notice = {"existing_application_notice": True, "reconciled_existing_submission": True}
    matched = {"matched_existing_application": True, "email": {"message_id": "m1", "sender_trusted": True, "authenticated": True}}
    stamp = lambda days: (now - timedelta(days=days)).isoformat()  # noqa: E731
    with service.db.session() as s:
        s.execute(insert(Job), [dict(id=n, identity=f"example.test/{n}", url=f"https://boards.example.test/{n}", title="Engineer",
                                     company="Example Co", location="Remote", ats="greenhouse", status="ready", demo=False,
                                     created_at=stamp(30)) for n in ("sent", "notice_ours", "notice_theirs", "mail_theirs", "quote_theirs", "both")])
        s.execute(insert(Run), [
            dict(id=r, job_id=j, state=st, mode="submit", reason="", packet={}, receipt=rc, created_at=stamp(d), finished_at=stamp(d))
            for r, j, st, rc, d in [
                ("r1", "sent", "confirmed", PAGE, 1),
                ("r2", "notice_ours", "submission_unknown", {}, 3),
                ("r3", "notice_ours", "confirmed", notice, 1),
                ("r4", "notice_theirs", "confirmed", notice, 1),
                ("r5", "mail_theirs", "confirmed", matched, 1),
                ("r6", "quote_theirs", "confirmed", quote, 1),
                # A real page first, then a later visit finds the employer's notice: the job's newest run is the notice, and it is ours.
                ("r7", "both", "confirmed", PAGE, 3),
                ("r8", "both", "confirmed", notice, 1),
            ]
        ])
        s.execute(insert(Event), [dict(run_id="r2", kind="submitting", message="", data={}, created_at=stamp(3))])
    listed = client.get("/api/successful-applications").json()["applications"]
    assert {r["job_id"] for r in listed if r["existing_application"]} == {"notice_theirs", "mail_theirs", "quote_theirs"}
    assert len(listed) == 6  # every one stays on the list: stored results are not rewritten, and the row says what proved it
    assert ledger_row(client, "notice_ours")["proof"] == "existing"  # what is on file is still the employer's page
    assert ledger_row(client, "mail_theirs")["proof"] == "email"
    score = client.get("/api/success").json()
    assert score["overall"]["confirmed"] == 3 and score["overall"]["already_applied"] == 3
    # What the CSV's "sent since" and the page's "Sent" filter keep is exactly what the score confirmed in the same window.
    assert sum(not r["existing_application"] for r in listed) == score["overall"]["confirmed"]
