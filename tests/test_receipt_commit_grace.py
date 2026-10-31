"""An acknowledgement may precede the logged commit event by a short grace, never the run itself. Synthetic data."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from jobpilot.db import Run
from jobpilot.receipts import acknowledgement, check_receipts, message_facts
from support_mail import EMAIL, receipt_seed as base_seed
from test_receipt_attribution import mailbox_provider, receipt, seed, submitted

BODY = "We received your application to Example for the Engineer position."


async def test_a_receipt_seconds_before_the_commit_event_still_counts(service, monkeypatch):
    """The "submitting" event is logged after the click: an iCIMS acknowledgement was delivered 5 s earlier (2026-10-29)."""
    at = datetime.now(UTC) - timedelta(hours=1)
    ids = seed(service, 1, state="submission_unknown", start=at)
    submitted(service, ids, [at + timedelta(minutes=5)])
    mailbox_provider(monkeypatch, service, [receipt("early", at + timedelta(minutes=5) - timedelta(seconds=5), BODY)])
    assert (await check_receipts(service.mail))["emails_confirmed"] == 1
    with service.db.session() as s:
        assert s.get(Run, ids[0]).state == "confirmed"


async def test_a_receipt_older_than_the_grace_before_the_commit_does_not_count(service, monkeypatch):
    at = datetime.now(UTC) - timedelta(hours=1)
    ids = seed(service, 1, state="submission_unknown", start=at)
    submitted(service, ids, [at + timedelta(minutes=10)])
    mailbox_provider(monkeypatch, service, [receipt("old", at + timedelta(minutes=10) - timedelta(minutes=3), BODY)])
    assert (await check_receipts(service.mail))["emails_confirmed"] == 0


async def test_the_grace_never_reaches_before_the_run_began(service, monkeypatch):
    at = datetime.now(UTC) - timedelta(hours=1)
    ids = seed(service, 1, state="submission_unknown", start=at)
    submitted(service, ids, [at + timedelta(seconds=30)])
    # 90 s before the commit is inside the grace, but 60 s before the run was even created.
    mailbox_provider(monkeypatch, service, [receipt("before-run", at - timedelta(seconds=60), BODY)])
    assert (await check_receipts(service.mail))["emails_confirmed"] == 0


def test_a_resume_has_been_received_subject_is_acknowledgement_wording():
    """A Workday tenant mailed "<Employer> your resume/CV has been received" and nothing else matched (2026-10-30)."""
    at = datetime.now(UTC) - timedelta(hours=1)
    run = {"created_at": at.isoformat(), "packet": {"profile": {"email": EMAIL}}}
    msg = receipt("resume-cv", at + timedelta(minutes=1), "Example your resume/CV has been received")
    assert message_facts(msg) is not None
    assert acknowledgement(msg, {"company": "Example", "title": "Engineer"}, run, allow_missing_title=True)
    # A question about a resume is not one.
    other = receipt("not-receipt", at + timedelta(minutes=1), "Please send your resume/CV so we can reach out")
    assert message_facts(other) is None


async def test_unknown_run_without_a_commit_event_still_needs_one(service, monkeypatch):
    at = datetime.now(UTC) - timedelta(hours=1)
    rid = base_seed(service, 1, state="submission_unknown", start=at)[0]
    mailbox_provider(monkeypatch, service, [receipt("any", at + timedelta(minutes=1), BODY)])
    assert (await check_receipts(service.mail))["emails_confirmed"] == 0
    with service.db.session() as s:
        assert s.scalar(select(Run.state).where(Run.id == rid)) == "submission_unknown"
