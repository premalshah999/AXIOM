"""A run parked on a durable email wait is `waiting_email`, not a failed `waiting_browser`
(docs/forensics/FAILURE_ANALYSIS.md, flow F9: 266 such runs on 143 jobs were counted, reported and messaged as failures).
"""

import pytest

from jobpilot.admission import QueueBlocked
from jobpilot.db import Job, MailChallenge, Outbox, Run
from jobpilot.mail import MailService

WAIT = {
    "state": "waiting_browser",
    "reason": "Waiting for the employer verification email; other applications continue",
    "blocker_type": "email_wait",
    "waiting_kind": "email",
    "wait_handle": {"challenge_id": "c1"},
}


def seed(service, name, state="running"):
    service.db.set_setting("application_budget", {"max_seconds": None})
    with service.db.exclusive() as s:
        s.add(Job(id=name, identity=name, url=f"https://careers.example.test/{name}", ats="oracle",
                  company="Acme", title="Engineer", status="running"))
        s.add(Run(id=name + "-run", job_id=name, mode="submit", state=state,
                  packet={"match_approved": True, "profile": {"autonomous": True}}))
    return name + "-run"


def outbox_kinds(service, run_id):
    with service.db.session() as s:
        return sorted(row.kind for row in s.query(Outbox).where(Outbox.run_id == run_id))


def test_a_durable_email_wait_finishes_as_waiting_email(service):
    run_id = seed(service, "parked")
    service.complete(run_id, dict(WAIT), 30.0)
    with service.db.session() as s:
        run = s.get(Run, run_id)
        assert run.state == "waiting_email" and s.get(Job, "parked").status == "waiting_email"
        assert run.packet["waiting_kind"] == "email" and run.packet["wait_handle"] == {"challenge_id": "c1"}


def test_a_parked_wait_files_no_failure_report_and_asks_no_repair(service, monkeypatch):
    monkeypatch.setattr(service.config, "failure_reports_enabled", True)
    parked, broken = seed(service, "parked"), seed(service, "broken")
    service.complete(parked, dict(WAIT), 30.0)
    service.complete(broken, {"state": "waiting_browser", "reason": "The page did not advance"}, 30.0)
    assert "report" not in outbox_kinds(service, parked) and "repair" not in outbox_kinds(service, parked)
    assert {"report", "repair"} <= set(outbox_kinds(service, broken))  # a real control failure still does both


def test_other_waiting_browser_results_keep_their_state(service):
    run_id = seed(service, "stuck")
    service.complete(run_id, {"state": "waiting_browser", "reason": "A control was rejected",
                              "blocker_type": "email_wait", "recovery_cause": "sender_busy"}, 20.0)
    with service.db.session() as s:
        assert s.get(Run, run_id).state == "waiting_browser"  # lease waits sent nothing and retry; no durable wait exists


def test_the_mail_service_still_recognises_the_owner_of_a_wait(service):
    run_id = seed(service, "parked")
    service.complete(run_id, dict(WAIT), 30.0)
    with service.db.session() as s:
        assert MailService.waiting_owner(s.get(Run, run_id), "c1")
        assert not MailService.waiting_owner(s.get(Run, run_id), "other")


async def test_a_matched_email_resumes_a_waiting_email_run(service):
    """Past the state gate the resume stops at the paused-control check; a state it did not accept returns quietly."""
    run_id = seed(service, "parked")
    service.complete(run_id, dict(WAIT), 30.0)
    service.db.set_setting("control", {"paused": True, "auto_submit": True})
    with service.db.exclusive() as s:
        s.add(MailChallenge(id="c1", run_id=run_id, rule_id="r", mailbox_id="m", recipient="alex@example.test",
                            kind="code", state="matched", since=0.0, expires=1e12))
    with pytest.raises(QueueBlocked) as caught:
        await service.resume_mail_wait(run_id, "c1")
    assert caught.value.code == "paused"
    other = seed(service, "failed", state="failed")
    with service.db.exclusive() as s:
        s.get(Run, other).packet = {**s.get(Run, other).packet, "wait_handle": {"challenge_id": "c1"},
                                    "waiting_kind": "email"}
    assert await service.resume_mail_wait(other, "c1") is None


def test_the_owner_may_still_apply_again_to_a_waiting_email_job():
    from jobpilot.service import RETRYABLE, TERMINAL

    assert "waiting_email" in RETRYABLE and "waiting_email" in TERMINAL


def test_a_parked_wait_is_not_a_failure_anywhere_failures_are_listed():
    from jobpilot.diagnostics import HELP_STATES

    assert "waiting_email" not in HELP_STATES  # no help screenshot for a wait
