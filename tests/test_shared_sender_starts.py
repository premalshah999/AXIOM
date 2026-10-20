"""Oracle Recruiting mails every employer's verification code from one sender (2026-10-18). Some runs stop because a
sibling held that sender, or because the app's browser, model provider or worker was unavailable. Those runs sent
nothing to the employer, so they are not among the job's three starts. A new Oracle start waits while another job
holds the sender, and the repair agent leaves these waits to the recovery scheduler."""

from datetime import UTC, datetime, timedelta

import pytest

from jobpilot.admission import QueueBlocked
from jobpilot.application_budget import require_allowance
from jobpilot.db import Job, Review, Run, Setting
from jobpilot.mail import RuleInput
from jobpilot.recovery import schedule_recovery
from jobpilot.repair_agent import OwnerQuestion, RepairPlan, repair_run
from support_mail import seed

AT = datetime(2026, 10, 18, 5, tzinfo=UTC)
BUSY = "Another application kept the same email sender busy; nothing was sent. Requeue this job"
INTERRUPTED = "Worker interrupted before submission; draft recovery scheduled"


def runs(service, job_id, rows):
    service.db.set_setting("application_budget", {"max_seconds": None})
    with service.db.exclusive() as s:
        s.add(Job(id=job_id, identity=job_id, url=f"https://acme.fa.oraclecloud.com/job/{job_id}", ats="oracle"))
        s.flush()
        for i, (state, reason) in enumerate(rows):
            s.add(Run(id=f"{job_id}-{i}", job_id=job_id, mode="submit", state=state, reason=reason,
                      started_at=(AT - timedelta(minutes=10 - i)).isoformat()))


def test_starts_that_sent_nothing_leave_the_three_attempts(service):
    runs(service, "ford", [("timed_out", BUSY)] * 3 + [("waiting_browser", INTERRUPTED)])
    with service.db.session() as s:
        require_allowance(s, "ford", at=AT)


def test_attempts_that_reached_the_employer_still_count(service):
    runs(service, "acme", [
        ("timed_out", BUSY),
        ("waiting_browser", "The browser did not advance within its recovery budget"),
        ("failed", "Worker error: RuntimeError"),
        # The request window was already open: mail may have been sent.
        ("submission_unknown", "Another application kept the same email sender busy; no verification request was sent"),
    ])
    with service.db.session() as s:
        with pytest.raises(QueueBlocked) as blocked:
            require_allowance(s, "acme", at=AT)
        assert blocked.value.code == "application_retry_limit"


@pytest.mark.parametrize("hours_ago,scheduled,waits", [(20, True, 1), (0, False, 3)])
def test_sender_waits_an_hour_apart_are_separate_attempts(service, hours_ago, scheduled, waits):
    with service.db.exclusive() as s:
        s.add(Job(id="hw", identity="hw", url="https://acme.fa.oraclecloud.com/job/hw", ats="oracle"))
        run = Run(id="hw-now", job_id="hw", mode="submit", state="timed_out", reason=BUSY,
                  packet={"match_approved": True, "profile": {"autonomous": True}})
        s.add(run)
        s.add(Setting(key="draft_recovery:hw", value={
            "cause": "sender_busy", "sender_waits": 2,
            "updated_at": (datetime.now(UTC) - timedelta(hours=hours_ago)).isoformat(),
        }))
        s.flush()
        schedule_recovery(s, service.config, run, {"recovery_cause": "sender_busy"})
        saved = s.get(Setting, "draft_recovery:hw").value
        assert saved["scheduled"] is scheduled and saved["sender_waits"] == waits


async def test_an_oracle_start_waits_only_for_its_own_tenants_code_window(service):
    # Oracle tenants mail codes under their own identity (2026-10-18), so other tenants proceed in parallel.
    box, holder = seed(service)
    rule = await service.mail.save_rule(RuleInput(
        mailbox_id=box["id"], employer_origin="https://first.fa.oraclecloud.com",
        sender_domains=["oraclecloud.com", "oracle.com"],
    ))
    with service.db.exclusive() as s:
        s.add(Job(id=holder["job_id"], identity="first", url="https://first.fa.oraclecloud.com/job/1", ats="oracle"))
        s.add(Job(id="sibling", identity="sibling", url="https://first.fa.oraclecloud.com/job/9", ats="oracle"))
        s.add(Job(id="second", identity="second", url="https://second.fa.oraclecloud.com/job/2", ats="oracle"))
        s.add(Job(id="wd", identity="wd", url="https://acme.wd5.myworkdayjobs.com/job/3", ats="workday"))
    challenge = service.mail.begin(holder["id"], rule, "alex@example.test", "code")
    with service.db.session() as s:
        assert not service.mail.shared_sender_free(s, s.get(Job, "sibling"))
        assert service.mail.shared_sender_free(s, s.get(Job, "second"))
        assert service.mail.shared_sender_free(s, s.get(Job, holder["job_id"]))  # its own continuation
        assert service.mail.shared_sender_free(s, s.get(Job, "wd"))
    service.mail.outcome(challenge["id"], "verified")
    with service.db.session() as s:
        assert service.mail.shared_sender_free(s, s.get(Job, "sibling"))


@pytest.mark.parametrize("reason", [BUSY, INTERRUPTED, "Open the ApplyPilot Chrome window: the browser is responding"])
async def test_the_repair_agent_leaves_the_apps_own_waits_to_recovery(service, monkeypatch, reason):
    async def fake_structured(*args, **kwargs):
        raise AssertionError("no diagnosis for a wait the recovery scheduler retries")

    monkeypatch.setattr("jobpilot.models.structured", fake_structured)
    with service.db.exclusive() as s:
        s.add(Job(id="wait", identity="wait", url="https://acme.fa.oraclecloud.com/job/w", ats="oracle"))
        s.add(Run(id="wait-run", job_id="wait", mode="submit", state="timed_out", reason=reason, packet={}))
    assert (await repair_run(service, "wait-run"))["skipped"] == "not repairable"


async def test_the_repair_agent_never_asks_the_owner_for_saved_contact_details(service, monkeypatch):
    plan = RepairPlan(
        diagnosis="The email address field is empty.",
        category="missing_fact",
        owner_question=OwnerQuestion(question="What email address should be used for the Honeywell (Oracle) application?"),
    )

    async def fake_structured(*args, **kwargs):
        return plan

    monkeypatch.setattr("jobpilot.models.structured", fake_structured)
    service.db.set_setting("profile", {"first_name": "Alex", "last_name": "Rivera"})
    with service.db.exclusive() as s:
        s.add(Job(id="hon", identity="hon", url="https://acme.fa.oraclecloud.com/job/h", ats="oracle"))
        s.add(Run(id="hon-run", job_id="hon", mode="submit", state="waiting_browser",
                  reason="The browser did not advance within its recovery budget", packet={}))
    (service.config.data_dir / "runs" / "hon-run").mkdir(parents=True)
    summary = await repair_run(service, "hon-run")
    assert summary["outcome"] != "asked_owner" and summary["question"] is None
    with service.db.session() as s:
        assert s.query(Review).filter(Review.run_id == "hon-run").count() == 0
        assert s.get(Run, "hon-run").state == "waiting_browser"
