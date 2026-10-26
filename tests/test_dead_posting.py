from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from jobpilot import telegram, tiers
from jobpilot.browser import BrowserEngine
from jobpilot.db import Job, Run
from jobpilot.job_control import manage
from jobpilot.workday import CLOSED_REASON, POSTING_MISSING_REASON, missing_posting_result

JOB_URL = "https://kla.wd1.myworkdayjobs.com/en-US/annarbor/job/Ann-Arbor-MI/Engr--Software-2_2640145"
# The saved step the app itself stored after a live run (the posting's /apply/applyManually).
STEP_URL = (
    "https://kla.wd1.myworkdayjobs.com/en-US/annarbor/job/Ann-Arbor%2C-MI/Engr--Software-2_2640145"
    "/apply/applyManually"
)


def job_row(job_id="j1"):
    return {"id": job_id, "ats": "workday", "url": JOB_URL}


def engine_with_saved_route(route_url):
    """A BrowserEngine shell whose sealed desktop route opens `route_url`."""
    eng = BrowserEngine.__new__(BrowserEngine)
    eng.job = job_row()
    eng.run_record = {"packet": {"resume_sha": "sha"}}
    sealed = {"url": route_url, "job_id": "j1", "resume_sha": "sha", "session_storage": {}}
    vault = SimpleNamespace(open=lambda token: dict(sealed), seal=lambda value: "token")
    eng.service = SimpleNamespace(mail=SimpleNamespace(vault=vault))
    return eng, {"resume_route": "token", "resume_sha": "sha"}


def test_saved_workday_step_is_not_replayed_as_the_entry_point():
    eng, saved = engine_with_saved_route(STEP_URL)
    assert eng.desktop_resume_route(saved) is None  # the caller falls back to the posting URL


def test_saved_workday_posting_page_is_still_reopened():
    eng, saved = engine_with_saved_route(JOB_URL)
    assert eng.desktop_resume_route(saved) == JOB_URL


def test_one_not_found_at_the_posting_url_never_closes(service):
    result = missing_posting_result(service.db, job_row(), "run-1", JOB_URL)
    assert result["state"] == "waiting_browser"
    assert result["recovery_cause"] == "posting_unavailable"
    assert result["reason"] == POSTING_MISSING_REASON


def test_two_distinct_runs_at_the_posting_url_close_the_job(service):
    missing_posting_result(service.db, job_row(), "run-1", JOB_URL)
    result = missing_posting_result(service.db, job_row(), "run-2", JOB_URL)
    assert result == {"state": "closed", "reason": CLOSED_REASON}


def test_the_same_run_observed_twice_counts_once(service):
    missing_posting_result(service.db, job_row(), "run-1", JOB_URL)
    result = missing_posting_result(service.db, job_row(), "run-1", JOB_URL)
    assert result["state"] == "waiting_browser"


def test_not_found_on_a_saved_step_never_counts_toward_closure(service):
    results = [missing_posting_result(service.db, job_row(), f"run-{i}", STEP_URL) for i in range(5)]
    assert all(r["state"] == "waiting_browser" for r in results)
    assert all(r["recovery_cause"] == "route_unavailable" for r in results)
    assert service.db.get_setting("posting_gone:j1", {}) == {}


def test_posting_missing_text_is_external_to_escalation():
    # The escalator skips runs whose reason matches EXTERNAL; it must match both reason texts.
    assert tiers.EXTERNAL.search(POSTING_MISSING_REASON)
    assert tiers.EXTERNAL.search(CLOSED_REASON)


def seed_job(service, job_id, status, reason, packet):
    old = (datetime.now(UTC) - timedelta(minutes=10)).isoformat()
    with service.db.session() as s:
        s.add(
            Job(
                id=job_id,
                identity=job_id,
                url=JOB_URL,
                ats="workday",
                status=status,
                company="KLA",
                title="Engineer",
            )
        )
        s.add(
            Run(
                id=job_id + "-1",
                job_id=job_id,
                state="waiting_browser",
                mode="submit",
                reason=reason,
                created_at=old,
                finished_at=old,
                packet={"match_approved": True, **packet},
            )
        )


async def test_closed_posting_is_not_resumed_without_override(service, monkeypatch):
    seed_job(service, "dead", "closed", CLOSED_REASON, {"recovery_cause": "posting_unavailable"})
    queue = AsyncMock(return_value={"id": "new-run"})
    monkeypatch.setattr(service, "queue", queue)
    with pytest.raises(ValueError, match="closed"):
        await manage(service, "dead", "resume")
    queue.assert_not_called()
    assert service.db.get_setting("job_control:dead", {}) == {}
    with service.db.session() as s:
        assert s.get(Job, "dead").status == "closed"
    # An explicit owner override is the only way back into the queue.
    result = await manage(service, "dead", "resume", reopen_closed=True)
    assert result["run_id"] == "new-run"
    queue.assert_awaited_once()


async def test_escalation_skips_a_job_whose_posting_is_missing(service, monkeypatch):
    seed_job(
        service,
        "missing",
        "waiting_browser",
        POSTING_MISSING_REASON,
        {"recovery_cause": "posting_unavailable", "runner_tier": 1},
    )
    manage_mock = AsyncMock(return_value={"state": "queued"})
    monkeypatch.setattr("jobpilot.job_control.manage", manage_mock)
    assert await tiers.escalate_due(service) == 0
    manage_mock.assert_not_awaited()


async def test_posting_not_found_is_one_telegram_message_per_job(service, monkeypatch):
    sent = []

    async def fake_api(config, method, payload):
        sent.append(payload["text"])
        return {"message_id": len(sent)}

    monkeypatch.setattr(telegram, "api", fake_api)
    job = Job(
        id="j1",
        identity="j1",
        url=JOB_URL,
        ats="workday",
        status="waiting_browser",
        company="KLA",
        title="Engineer",
    )
    for n, engine_version in enumerate(("v1", "v2")):
        run = Run(
            id=f"r{n}",
            job_id="j1",
            state="waiting_browser",
            mode="submit",
            reason=POSTING_MISSING_REASON,
            created_at=f"2026-10-13T0{n}:00:00+00:00",
            packet={"recovery_cause": "posting_unavailable", "engine_version": engine_version},
        )
        await telegram.notify_outcome(service, run, job)
    assert len(sent) == 1
    assert "not found" in sent[0] and "not retried" in sent[0]
    assert "repair" not in sent[0]


def test_smartrecruiters_expired_job_ad_is_closed():
    """Woongjin 2026-10-14: 'This job ad has expired' was retried four times instead of closing."""
    from jobpilot.adapters.base import CLOSED

    assert CLOSED.search("This job ad has expired")
    assert CLOSED.search("This job listing has been removed")


async def test_outcomes_the_app_retries_send_no_telegram_message(service, monkeypatch):
    # Owner 2026-10-19: "I still see a lot of timeouts or related errors on telegram." Deploy interruptions and
    # stalls the autopilot retries need nothing from the owner; a spent job still reports.
    sent = []

    async def fake_api(config, method, payload):
        sent.append(payload["text"])
        return {"message_id": len(sent)}

    monkeypatch.setattr(telegram, "api", fake_api)
    with service.db.exclusive() as s:
        s.add(Job(id="gh", identity="gh", url="https://boards.greenhouse.io/acme/jobs/1", ats="greenhouse",
                  status="waiting_browser", company="Acme", title="Data Scientist"))
    interrupted = Run(id="i1", job_id="gh", state="waiting_browser", mode="submit",
                      reason="Worker interrupted before submission; draft recovery scheduled",
                      created_at="2026-10-19T04:00:00+00:00", packet={"engine_version": "v1"})
    stalled = Run(id="s1", job_id="gh", state="timed_out", mode="submit",
                  reason="Application exceeded the 600-second execution budget",
                  created_at="2026-10-19T04:10:00+00:00", packet={"engine_version": "v2"})
    job = Job(id="gh", identity="gh", url="https://boards.greenhouse.io/acme/jobs/1", ats="greenhouse",
              status="waiting_browser", company="Acme", title="Data Scientist")
    await telegram.notify_outcome(service, interrupted, job)
    await telegram.notify_outcome(service, stalled, job)
    assert sent == []
    with service.db.exclusive() as s:
        for n in range(3):
            s.add(Run(id=f"spent{n}", job_id="gh", state="timed_out", mode="submit",
                      reason="Application exceeded the 600-second execution budget",
                      created_at=f"2026-10-19T03:0{n}:00+00:00", started_at=f"2026-10-19T03:0{n}:00+00:00"))
    await telegram.notify_outcome(service, stalled, job)
    assert sent == []  # A timeout is the app's to retry; Telegram carries results only (owner 2026-10-26).
