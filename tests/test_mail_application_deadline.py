"""Lease contention must not spend browser attempts or outlive its application."""
import time
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from jobpilot.db import MailChallenge, Run
from support_mail import configured


def set_deadline(service, run_id, deadline):
    with service.db.exclusive() as session:
        run = session.get(Run, run_id)
        run.packet = {**run.packet, "application_deadline": datetime.fromtimestamp(deadline, UTC).isoformat()}


async def test_new_and_durable_mail_windows_share_application_deadline(service):
    _, run, rule = await configured(service)
    deadline = time.time() + 20
    set_deadline(service, run["id"], deadline)
    challenge = service.mail.begin(run["id"], rule, "alex@example.test", "code")
    assert challenge["expires"] == pytest.approx(deadline)
    extended = service.mail.persist_wait(challenge["id"])
    assert extended["expires"] == pytest.approx(deadline)
    assert extended["since"] == challenge["since"]


async def test_expired_application_cannot_send_or_consume_mail(service):
    _, run, rule = await configured(service)
    challenge = service.mail.begin(run["id"], rule, "alex@example.test", "code")
    with service.db.exclusive() as session:
        row = session.get(MailChallenge, challenge["id"])
        row.state = "matched"
        row.payload = service.mail.vault.seal({"kind": "code", "value": "fixture-code"})
    set_deadline(service, run["id"], time.time() - 1)
    with pytest.raises(ValueError, match="deadline"):
        service.mail.begin(run["id"], rule, "alex@example.test", "code")
    assert service.mail.claim(challenge["id"]) is None
    with service.db.session() as session:
        row = session.get(MailChallenge, challenge["id"])
        assert row.state == "expired" and row.payload == ""


async def test_known_sender_blocker_is_checked_before_browser_retry(service):
    _, run, rule = await configured(service)
    challenge = service.mail.begin(run["id"], rule, "alex@example.test", "code")
    job = SimpleNamespace(url="https://careers.example.test/job/2")
    recovery = {"cause": "sender_busy", "source_run_id": "another-run"}
    with service.db.session() as session:
        assert not service.mail.recovery_sender_ready(session, job, recovery)
        assert service.mail.recovery_sender_ready(session, job, {"cause": "control_observation"})
        assert service.mail.recovery_sender_ready(session, job, {**recovery, "source_run_id": run["id"]})
        assert session.get(MailChallenge, challenge["id"]).run_id == run["id"]
    service.mail.outcome(challenge["id"], "verified")
    with service.db.session() as session:
        assert service.mail.recovery_sender_ready(session, job, recovery)


async def test_expired_owner_does_not_hold_sender_until_background_sweep(service):
    _, run, rule = await configured(service)
    service.mail.begin(run["id"], rule, "alex@example.test", "code")
    set_deadline(service, run["id"], time.time() - 1)
    with service.db.session() as session:
        assert service.mail.sender_blocker(session, rule, "other-run") is None


def test_sender_retry_delay_fits_total_budget(service):
    from jobpilot.db import Job, Setting
    from jobpilot.recovery import schedule_recovery
    with service.db.exclusive() as session:
        session.add(Job(id="job", identity="job", url="https://example.test/1", ats="oracle"))
        run = Run(id="run", job_id="job", mode="submit", state="timed_out",
                  packet={"match_approved": True, "profile": {"autonomous": True}})
        session.add(run)
        session.flush()
        before = time.time()
        schedule_recovery(session, service.config, run, {"recovery_cause": "sender_busy"})
        saved = session.get(Setting, "draft_recovery:job").value
        delay = datetime.fromisoformat(saved["not_before"]).timestamp() - before
        assert saved["scheduled"] and 14 <= delay <= 16


@pytest.mark.parametrize("kind,blocked", [("code", True), ("password_reset", False)])
async def test_shared_code_lease_stays_serial_but_exact_distinct_links_can_overlap(service, kind, blocked):
    from jobpilot.db import MailRule
    _, run, rule = await configured(service)
    with service.db.exclusive() as session:
        session.get(MailRule, rule["id"]).link_origins = [rule["employer_origin"]]
        session.add(Run(id="other", job_id="other-job", state="running"))
    rule = service.mail.rule_for(rule["employer_origin"])
    challenge = service.mail.begin(run["id"], rule, "alex@example.test", kind)
    other_rule = {**rule, "employer_origin": "https://second.example.test",
                  "link_origins": ["https://second.example.test"]}
    with service.db.session() as session:
        assert bool(service.mail.sender_blocker(session, other_rule, "other", kind)) is blocked
        assert session.get(MailChallenge, challenge["id"]).state == "pending"


async def test_background_sweep_expires_application_window_without_reading_mail(service, monkeypatch):
    _, run, rule = await configured(service)
    challenge = service.mail.begin(run["id"], rule, "alex@example.test", "code")
    set_deadline(service, run["id"], time.time() - 1)
    async def no_request(*args, **kwargs):
        raise AssertionError("Expired application must not poll the mailbox")
    monkeypatch.setattr(service.mail, "request", no_request)
    await service.mail.poll_once()
    with service.db.session() as session:
        assert session.get(MailChallenge, challenge["id"]).state == "expired"


async def test_queued_sender_retry_never_claims_browser_while_blocked(service, monkeypatch):
    from jobpilot.service import execution as service_module
    from jobpilot.db import Job, Setting, now
    _, holder, rule = await configured(service)
    service.mail.begin(holder["id"], rule, "alex@example.test", "code")
    with service.db.exclusive() as session:
        session.add(Job(id="target", identity="target", url="https://careers.example.test/job/2", ats="oracle"))
        session.add(Run(id="source", job_id="target", mode="submit", state="timed_out", started_at=now()))
        session.add(Run(id="queued", job_id="target", mode="submit", state="queued"))
        session.add(Setting(key="draft_recovery:target", value={"cause": "sender_busy", "source_run_id": "source"}))
    slept = []
    async def stop_after_wait(seconds):
        slept.append(seconds)
        with service.db.exclusive() as session:
            row = session.get(Run, "queued")
            assert row.state == "queued" and row.started_at is None
            row.state = "paused"  # End the fixture after proving admission did not consume a start.
    monkeypatch.setattr(service_module, "asyncio", SimpleNamespace(sleep=stop_after_wait))
    await service._execute_owned("queued")
    assert slept == [1]


def park(service, run_id, challenge_id, state):
    from jobpilot.db import Job

    with service.db.exclusive() as session:
        run = session.get(Run, run_id)
        if not session.get(Job, run.job_id):
            session.add(Job(id=run.job_id, identity=run.job_id, url="https://careers.example.test/job/1"))
            session.flush()
        run.state = "waiting_email"
        run.packet = {**run.packet, "waiting_kind": "email",
                      "wait_handle": {"challenge_id": challenge_id, "kind": "password_reset"}}
        session.get(Job, run.job_id).status = "waiting_email"
        session.get(MailChallenge, challenge_id).state = state


async def test_a_parked_run_whose_email_wait_ended_becomes_a_browser_recovery(service):
    """Intuit 2026-10-27: the reset email never came, the challenge expired, and the run stayed parked for hours."""
    from jobpilot.db import Job

    _, run, rule = await configured(service)
    challenge = service.mail.begin(run["id"], rule, "alex@example.test", "password_reset")
    park(service, run["id"], challenge["id"], "pending")
    with service.db.exclusive() as session:
        assert service.mail.end_dead_email_waits(session) == []
    park(service, run["id"], challenge["id"], "expired")
    with service.db.exclusive() as session:
        ended = service.mail.end_dead_email_waits(session)
    assert [run_id for run_id, _ in ended] == [run["id"]]
    with service.db.session() as session:
        row = session.get(Run, run["id"])
        assert row.state == "waiting_browser" and "password reset email did not arrive" in row.reason
        assert session.get(Job, row.job_id).status == "waiting_browser"


async def test_a_superseded_parked_run_is_left_alone(service):
    _, run, rule = await configured(service)
    challenge = service.mail.begin(run["id"], rule, "alex@example.test", "code")
    park(service, run["id"], challenge["id"], "expired")
    with service.db.exclusive() as session:
        old = session.get(Run, run["id"])
        session.add(Run(job_id=old.job_id, mode=old.mode, resume_id=old.resume_id, packet={},
                        created_at="9999-01-01T00:00:00+00:00"))
    with service.db.exclusive() as session:
        assert service.mail.end_dead_email_waits(session) == []
    with service.db.session() as session:
        assert session.get(Run, run["id"]).state == "waiting_email"
