"""Receipt first: an uncertain submission is resubmitted only after the mailbox was searched, to the end, for the
employer's acknowledgement of that very application. Asana, Assured Guaranty and Perseus (2026-10-12): the commit step
ran, no receipt was captured, and the policy sent the same application again 1 to 18 hours later; each employer's
acknowledgement had arrived within two minutes of its commit, but the background matcher (20 runs a pass) had not
reached those runs. Synthetic data."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from jobpilot import autopilot
from jobpilot.admission import QueueBlocked, unknown_resubmit_allowed
from jobpilot.autopilot import resubmit_unknown, unknown_candidates
from jobpilot.db import Job, Run, Setting
from jobpilot.mail_errors import MailTransportError
from jobpilot.receipts import employer_acknowledged
from support_mail import receipt_seed
from test_receipt_attribution import mailbox_provider, receipt, seed

NOW = datetime(2026, 10, 12, 20, 0, tzinfo=UTC)
BODY = "We received your application to Example for the Engineer position."


def run(minutes_ago=90, run_id="r1", job_id="j1"):
    at = (NOW - timedelta(minutes=minutes_ago)).isoformat()
    return SimpleNamespace(id=run_id, job_id=job_id, state="submission_unknown", receipt={}, finished_at=at, created_at=at)


class Session:
    def __init__(self, check=None):
        self.rows = {"unknown_resubmit_policy": {"enabled": True}}
        if check is not None:
            self.rows["unknown_receipt_check:j1"] = check

    def get(self, model, key):
        return SimpleNamespace(value=self.rows[key]) if key in self.rows else None


def checked(minutes_ago, run_id="r1"):
    return {"run_id": run_id, "at": (NOW - timedelta(minutes=minutes_ago)).isoformat()}


# ---------- the gate every path into a run goes through ----------


def test_a_resubmission_needs_a_receipt_check_of_the_newest_uncertain_run():
    assert not unknown_resubmit_allowed(Session(), [run()], NOW)
    assert unknown_resubmit_allowed(Session(), [run()], NOW, checked=False)  # What the autopilot asks before it checks.
    assert unknown_resubmit_allowed(Session(checked(10)), [run()], NOW)


def test_a_check_of_an_older_run_a_stale_check_or_one_inside_the_receipt_wait_does_not_count():
    assert not unknown_resubmit_allowed(Session(checked(10, run_id="earlier")), [run()], NOW)
    assert not unknown_resubmit_allowed(Session(checked(61)), [run()], NOW)
    assert not unknown_resubmit_allowed(Session(checked(80)), [run()], NOW)  # Made 10 minutes after the run ended.


async def test_the_queue_refuses_an_uncertain_job_nobody_checked_the_mailbox_for(service):
    """Every caller (the autopilot, a resume, a recovery retry, an owner click) is admitted by the same function."""
    service.db.set_setting("unknown_resubmit_policy", {"enabled": True})
    at = (datetime.now(UTC) - timedelta(hours=3)).isoformat()
    with service.db.exclusive() as s:
        s.add(Job(id="u", identity="u", url="https://boards.greenhouse.io/acme/jobs/41", ats="greenhouse", company="Acme",
                  status="submission_unknown"))
        s.add(Run(id="ur", job_id="u", mode="submit", state="submission_unknown", created_at=at, finished_at=at, started_at=at))
    with pytest.raises(QueueBlocked) as err:
        await service.queue("u", "submit", retry=True)
    assert err.value.code == "submitted_or_uncertain"
    service.db.set_setting("unknown_receipt_check:u", {"run_id": "ur", "at": datetime.now(UTC).isoformat()})
    try:
        await service.queue("u", "submit", retry=True)
    except QueueBlocked as late:  # Later gates may hold it; not this one.
        assert late.code != "submitted_or_uncertain"
    except ValueError:
        pass


# ---------- the mailbox search ----------


async def test_an_acknowledgement_the_matcher_has_not_attributed_still_stops_the_resubmission(service, monkeypatch):
    at = datetime.now(UTC) - timedelta(hours=2)
    (rid,) = seed(service, 1, state="submission_unknown", start=at)
    mailbox_provider(monkeypatch, service, [receipt("ack", at + timedelta(minutes=1), BODY)])
    assert await employer_acknowledged(service.mail, rid) is True


async def test_a_mailbox_searched_to_the_end_without_an_acknowledgement_clears_the_run(service, monkeypatch):
    at = datetime.now(UTC) - timedelta(hours=2)
    (rid,) = seed(service, 1, state="submission_unknown", start=at)
    mailbox_provider(monkeypatch, service, [receipt("elsewhere", at + timedelta(minutes=1), "A code for Other Corp: 123456")])
    assert await employer_acknowledged(service.mail, rid) is False


async def test_a_mail_before_the_first_commit_is_not_an_acknowledgement(service, monkeypatch):
    at = datetime.now(UTC) - timedelta(hours=2)
    (rid,) = seed(service, 1, state="submission_unknown", start=at)
    mailbox_provider(monkeypatch, service, [receipt("old", at - timedelta(minutes=30), BODY)])
    assert await employer_acknowledged(service.mail, rid) is False


async def test_a_mailbox_that_cannot_be_searched_answers_nothing(service, monkeypatch):
    at = datetime.now(UTC) - timedelta(hours=2)
    (rid,) = seed(service, 1, state="submission_unknown", start=at)

    async def down(box, path, params, background=False):
        raise MailTransportError("unreachable")

    monkeypatch.setattr(service.mail, "request", down)
    assert await employer_acknowledged(service.mail, rid) is None


async def test_no_mailbox_connected_answers_nothing(service):
    (rid,) = receipt_seed(service, 1, state="submission_unknown", start=datetime.now(UTC) - timedelta(hours=2))
    with service.db.exclusive() as s:
        from jobpilot.db import Mailbox

        s.query(Mailbox).delete()
    assert await employer_acknowledged(service.mail, rid) is None


# ---------- the autopilot ----------


def uncertain(service, minutes_ago=120):
    at = datetime.now(UTC) - timedelta(minutes=minutes_ago)
    (rid,) = seed(service, 1, state="submission_unknown", start=at)
    with service.db.exclusive() as s:
        job_id = s.get(Run, rid).job_id
        s.get(Job, job_id).status = "submission_unknown"
        s.get(Run, rid).finished_at = (at + timedelta(minutes=5)).isoformat()
    folder = service.config.data_dir / "runs" / rid
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "final_outline.txt").write_text("Apply for Engineer\nFirst Name\nSubmit application")
    service.db.set_setting("unknown_resubmit_policy", {"enabled": True})
    return job_id, rid, at


def queued_jobs(service, monkeypatch):
    queued = []

    async def queue(job_id, mode, *args, **kwargs):
        queued.append(job_id)
        return {"id": "again"}

    monkeypatch.setattr(service, "queue", queue)
    return queued


async def test_the_autopilot_holds_an_application_whose_acknowledgement_is_in_the_mailbox(service, monkeypatch):
    """The three incidents: a receipt-less commit, no matcher attribution yet, an acknowledgement already delivered."""
    job_id, rid, at = uncertain(service)
    queued = queued_jobs(service, monkeypatch)
    mailbox_provider(monkeypatch, service, [receipt("ack", at + timedelta(minutes=2), BODY)])
    assert await resubmit_unknown(service, 1) == 0
    assert queued == []
    with service.db.session() as s:
        assert s.get(Setting, "unknown_triage:" + job_id).value["verdict"] == "submitted"
        assert s.get(Setting, "unknown_receipt_check:" + job_id) is None


async def test_the_autopilot_resubmits_after_a_clean_search_and_records_the_check(service, monkeypatch):
    job_id, rid, at = uncertain(service)
    queued = queued_jobs(service, monkeypatch)
    mailbox_provider(monkeypatch, service, [])
    assert await resubmit_unknown(service, 1) == 1
    assert queued == [job_id]
    with service.db.session() as s:
        assert s.get(Setting, "unknown_receipt_check:" + job_id).value["run_id"] == rid


async def test_the_autopilot_never_resubmits_blind_when_the_mailbox_is_down(service, monkeypatch):
    job_id, rid, at = uncertain(service)
    queued = queued_jobs(service, monkeypatch)

    async def down(box, path, params, background=False):
        raise MailTransportError("unreachable")

    monkeypatch.setattr(service.mail, "request", down)
    assert await resubmit_unknown(service, 1) == 0
    assert queued == []
    with service.db.session() as s:
        assert s.get(Setting, "unknown_receipt_check:" + job_id) is None
        assert s.get(Setting, "unknown_triage:" + job_id) is None  # Not submitted, not cleared: asked again next tick.


async def test_one_tick_searches_the_mailbox_for_at_most_as_many_jobs_as_it_has_slots(service, monkeypatch):
    for n in range(3):
        with service.db.exclusive() as s:
            at = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
            s.add(Job(id=f"u{n}", identity=f"u{n}", url=f"https://boards.greenhouse.io/acme{n}/jobs/{n}", ats="greenhouse",
                      company=f"Acme {n}", status="submission_unknown"))
            s.add(Run(id=f"ur{n}", job_id=f"u{n}", mode="submit", state="submission_unknown", created_at=at, finished_at=at,
                      packet={"profile": {"email": "fixture@example.test"}}))
        folder = service.config.data_dir / "runs" / f"ur{n}"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "final_outline.txt").write_text("Submit application")
    service.db.set_setting("unknown_resubmit_policy", {"enabled": True})
    searched = []

    async def employer_checked(mail, run_id):
        searched.append(run_id)
        return None  # Cannot say: the next candidate must not be tried in the same tick.

    monkeypatch.setattr("jobpilot.receipts.employer_acknowledged", employer_checked)
    assert await resubmit_unknown(service, 1) == 0
    assert len(searched) == 1


def test_the_candidates_before_the_check_are_the_ones_the_policy_allows(service):
    job_id, rid, at = uncertain(service)
    assert unknown_candidates(service.db, checked=False) == [(job_id, rid)]
    assert unknown_candidates(service.db) == []  # Until the mailbox has been searched for it.
    service.db.set_setting("unknown_receipt_check:" + job_id, {"run_id": rid, "at": datetime.now(UTC).isoformat()})
    assert unknown_candidates(service.db) == [(job_id, rid)]
    assert autopilot.UNKNOWN_PER_TICK == 1
