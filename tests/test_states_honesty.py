"""Run states that told the truth late or never (2026-10-29): email waits a newer run had replaced, retry storms on a
model outage, sender-busy stops that needed a manual requeue, closed postings and owner-only stops in Repairing.
Everything here is synthetic."""

from datetime import UTC, datetime, timedelta

import pytest

from jobpilot.api import pipeline
from jobpilot.autopilot import persist_stalled
from jobpilot.db import Job, MailChallenge, Run, Setting
from jobpilot.operations.closed_postings import close_gone_postings
from jobpilot.operations.superseded import close_superseded_waits
from jobpilot.recovery import (
    WAIT_BACKOFF,
    backoff_seconds,
    nothing_sent_streak,
    repeats_unchanged,
    schedule_recovery,
)
from jobpilot.service.completion import CLOSED_POSTING

BUSY = "Another application kept the same email sender busy; nothing was sent. Requeue this job"
PROVIDER = "The selected model provider is unavailable; draft retained without further model requests"


def at(minutes):
    return (datetime(2026, 10, 28, 12, tzinfo=UTC) + timedelta(minutes=minutes)).isoformat()


def seed_job(service, job_id="j1", ats="workday"):
    with service.db.exclusive() as s:
        s.add(Job(id=job_id, identity=job_id, url=f"https://example.test/{job_id}", company="ExampleSoft",
                  title="Software Engineer", ats=ats, status="ready"))


def add_run(service, run_id, job_id, state, minute, reason="", packet=None):
    with service.db.exclusive() as s:
        s.add(Run(id=run_id, job_id=job_id, state=state, mode="submit", reason=reason, packet=packet or {},
                  created_at=at(minute), finished_at=at(minute)))


def add_window(service, run_id, state="pending"):
    with service.db.exclusive() as s:
        s.add(MailChallenge(id="c-" + run_id, run_id=run_id, rule_id="r", mailbox_id="m", recipient="alex@example.test",
                            kind="code", state=state, since=0.0, expires=9e12))


def state_of(service, run_id):
    with service.db.session() as s:
        return s.get(Run, run_id).state


def window_state(service, run_id):
    with service.db.session() as s:
        return s.get(MailChallenge, "c-" + run_id).state


def test_an_email_wait_a_newer_run_replaced_is_closed_with_its_window(service):
    seed_job(service)
    add_run(service, "old", "j1", "waiting_email", 0, packet={"wait_handle": {"challenge_id": "c-old"}})
    add_window(service, "old")
    add_run(service, "new", "j1", "timed_out", 5)
    assert close_superseded_waits(service) == ["old"]
    assert state_of(service, "old") == "cancelled" and window_state(service, "old") == "cancelled"
    assert state_of(service, "new") == "timed_out"  # the newest run is never touched


def test_the_latest_email_wait_is_a_real_wait(service):
    seed_job(service)
    add_run(service, "only", "j1", "waiting_email", 0, packet={"wait_handle": {"challenge_id": "c-only"}})
    add_window(service, "only")
    assert close_superseded_waits(service) == []
    assert state_of(service, "only") == "waiting_email" and window_state(service, "only") == "pending"


def test_a_wait_stays_while_a_newer_run_is_active_or_parked_on_its_window(service):
    seed_job(service, "j1")
    add_run(service, "old", "j1", "waiting_email", 0, packet={"wait_handle": {"challenge_id": "c-old"}})
    add_window(service, "old")
    add_run(service, "cont", "j1", "running", 5)  # the continuation that owns the matched email
    assert close_superseded_waits(service) == []
    seed_job(service, "j2")
    add_run(service, "old2", "j2", "waiting_email", 0, packet={"wait_handle": {"challenge_id": "c-old2"}})
    add_run(service, "again", "j2", "waiting_email", 5, packet={"wait_handle": {"challenge_id": "c-old2"}})
    assert close_superseded_waits(service, "j2", keep={"c-old2"}) == []


def test_finishing_a_run_closes_the_earlier_wait_of_its_job(service):
    seed_job(service)
    add_run(service, "old", "j1", "waiting_email", 0, packet={"wait_handle": {"challenge_id": "c-old"}})
    add_window(service, "old")
    add_run(service, "new", "j1", "running", 5)
    service.complete("new", {"state": "timed_out", "reason": "x"}, 1.0)
    assert state_of(service, "old") == "cancelled" and window_state(service, "old") == "cancelled"


def test_backoff_doubles_to_a_cap():
    first, longest, _ = WAIT_BACKOFF["provider_unavailable"]
    assert [backoff_seconds(n, first, longest) for n in (1, 2, 3, 4, 6, 9)] == [60, 120, 240, 480, 1800, 1800]


@pytest.mark.parametrize("cause,waits_before,scheduled", [
    ("provider_unavailable", 50, True),   # an outage never gives up; it backs off to 30 minutes
    ("sender_busy", 3, True),             # the lease frees by itself: no manual "Requeue this job"
    ("sender_busy", 40, False),
])
def test_waits_that_sent_nothing_reschedule_with_backoff(service, cause, waits_before, scheduled):
    seed_job(service)
    add_run(service, "r", "j1", "timed_out", 0, reason=BUSY, packet={"match_approved": True, "profile": {"autonomous": True}})
    with service.db.exclusive() as s:
        s.add(Setting(key="draft_recovery:j1", value={
            "cause": cause, "sender_waits": waits_before, "updated_at": datetime.now(UTC).isoformat()}))
        s.flush()
        schedule_recovery(s, service.config, s.get(Run, "r"), {"recovery_cause": cause})
        saved = s.get(Setting, "draft_recovery:j1").value
    assert saved["scheduled"] is scheduled and saved["sender_waits"] == waits_before + 1
    if scheduled:
        wait = datetime.fromisoformat(saved["not_before"]) - datetime.now(UTC)
        assert wait.total_seconds() > min(WAIT_BACKOFF[cause][1], 15 * 2 ** (waits_before)) - 5 or wait.total_seconds() > 250


def test_streak_counts_trailing_runs_that_sent_nothing():
    rows = [("waiting_browser", "Recovery stopped: x"), ("waiting_browser", PROVIDER), ("timed_out", BUSY)]
    assert nothing_sent_streak(rows) == 2 and nothing_sent_streak(rows[:1]) == 0


def test_the_same_stall_on_the_same_engine_is_not_repeated():
    stall = "Recovery stopped: Required field incomplete: Country; draft saved."
    assert repeats_unchanged([(stall, "e1"), (stall, "e1")])
    assert not repeats_unchanged([(stall, "e1"), (stall, "e2")])          # a new engine tries again
    assert not repeats_unchanged([(stall, "e1")])
    assert not repeats_unchanged([("The posting loads slowly", "e1")] * 2)  # only deterministic stalls
    assert not repeats_unchanged([(stall, None)] * 2)


async def test_the_autopilot_backs_off_a_model_outage_and_a_repeated_stall(service, monkeypatch):
    resumed = []

    async def fake_manage(service, job_id, action, **kwargs):
        resumed.append(job_id)
        return {"run_id": "r"}

    monkeypatch.setattr("jobpilot.job_control.manage", fake_manage)
    monkeypatch.setattr("jobpilot.autopilot.datetime", type("D", (), {"now": staticmethod(lambda tz=None: datetime(2026, 10, 28, 12, 30, tzinfo=UTC))}))
    stall = "Recovery stopped: Required field incomplete: Country; draft saved."
    for job_id, rows in {
        # three model failures, the last 4 minutes ago: waits 4 minutes (1, 2, 4), so not yet
        "storm": [("waiting_browser", PROVIDER, 20), ("waiting_browser", PROVIDER, 24), ("waiting_browser", PROVIDER, 26)],
        # one model failure 4 minutes ago: a minute is enough
        "blip": [("waiting_browser", PROVIDER, 26)],
        "stuck": [("waiting_browser", stall, 5), ("waiting_browser", stall, 10)],
    }.items():
        seed_job(service, job_id, "oracle")
        for i, (state, reason, minute) in enumerate(rows):
            add_run(service, f"{job_id}{i}", job_id, state, minute, reason, {"engine_version": "e1"})
    await persist_stalled(service, 10, ["Software Engineer"], {})
    assert resumed == ["blip"]


@pytest.mark.parametrize("reason", [
    "The Ashby application URL now returns 'Page not found' and the repair instruction states the posting is gone.",
    "The employer reports that this posting page does not exist; availability must be checked",
    "The job posting at this URL is closed: the job you are trying to apply for has been filled.",
    "The requisition URL returns a 404; the job posting no longer exists and cannot be applied to.",
    "Paylocity returns 'that job does not exist or is not currently active'.",
])
def test_a_reason_that_says_the_posting_is_gone_is_recognised(reason):
    assert CLOSED_POSTING.search(reason)


@pytest.mark.parametrize("reason", [
    "The browser tab closed unexpectedly; the application will be retried",
    "Recovery stopped: Required field incomplete: Country; draft saved.",
    "All required fields have been filled but Submit is covered",
    "This employer has an account for your email but refuses the saved password",
])
def test_a_form_stall_is_not_a_closed_posting(reason):
    assert not CLOSED_POSTING.search(reason)


def test_a_finished_run_whose_reason_says_the_posting_is_gone_is_closed(service):
    seed_job(service)
    add_run(service, "r", "j1", "running", 0)
    service.complete("r", {"state": "waiting_browser", "reason": "The job posting no longer exists."}, 1.0)
    with service.db.session() as s:
        assert s.get(Run, "r").state == "closed" and s.get(Job, "j1").status == "closed"


def test_stopped_runs_from_before_the_rule_are_closed_once(service):
    seed_job(service, "gone")
    add_run(service, "g", "gone", "waiting_browser", 0, "The posting is gone; stop attempting this application.")
    seed_job(service, "live")
    add_run(service, "l", "live", "waiting_browser", 0, "Recovery stopped: Required field incomplete: Country; draft saved.")
    assert close_gone_postings(service.db) == ["g"] and close_gone_postings(service.db) == []
    assert state_of(service, "l") == "waiting_browser"


def groups(service):
    with service.db.session() as s:
        rows = pipeline.classified()
        return dict(s.execute(pipeline.select(rows.c.job_id, rows.c.grp)).all())


def test_repairing_holds_only_what_the_engine_can_still_fix(service):
    cases = {
        "mail": ("waiting_browser", "The employer's password reset email did not arrive in time; the draft is kept"),
        "persona": ("waiting_browser", "The application is blocked at Amazon's Persona identity-verification page."),
        "password": ("waiting_browser", "This employer refuses the saved password. Reset the password in your own browser"),
        "busy": ("timed_out", BUSY),
        "model": ("waiting_browser", PROVIDER),
        "stall": ("waiting_browser", "Recovery stopped: Required field incomplete: Country; draft saved."),
        "botwall": ("waiting_browser", "A platform bot check paused SmartRecruiters application"),
    }
    for name, (state, reason) in cases.items():
        seed_job(service, name)
        add_run(service, "run-" + name, name, state, 0, reason)
    found = groups(service)
    assert found["persona"] == found["password"] == "needs"
    assert found["busy"] == found["model"] == "active"
    assert found["mail"] == found["stall"] == found["botwall"] == "repair"


def test_an_explicit_refusal_page_permits_the_validation_retry_even_without_field_names(service):
    """JazzHR's "please correct the errors" page names no field (9 runs on 2 jobs, 2026-10-29)."""
    seed_job(service)
    service.db.set_setting("control", {"paused": False, "auto_submit": True})
    add_run(service, "r", "j1", "running", 0)
    with service.db.exclusive() as s:
        s.get(Run, "r").state = "submitting"
    page = {"type": "explicit_rejection_page", "category": "validation", "evidence": "Please correct the errors",
            "missing_fields": []}
    service.authorize_validation_retry("r", page)
    for silent in ({}, {"category": "validation", "missing_fields": []}, {**page, "category": "rejection"}):
        with pytest.raises(ValueError, match="No explicit"):
            service.authorize_validation_retry("r", silent)


def test_a_repeated_stall_has_one_signature_whatever_the_last_refused_click(service):
    from jobpilot.recovery import failure_signature

    run = Run(id="x", job_id="j1", packet={})
    reason = "Recovery stopped: Required field incomplete: Country; draft saved."

    def result(state):
        return {"reason": reason, "recovery_cause": "control_observation", "blocker_type": "validation",
                "browser_checkpoint": {"failed_strategies": {"fill:country": {"attempts": [{"state": state}]}}}}

    assert failure_signature(run, result("refused")) == failure_signature(run, result("unchanged"))


def test_a_stall_that_repeats_stops_and_records_the_blocker(service):
    seed_job(service)
    packet = {"match_approved": True, "profile": {"autonomous": True}, "engine_version": "e1"}
    add_run(service, "a", "j1", "waiting_browser", 0, packet=packet)
    reason = "Recovery stopped: Required field incomplete: Country; draft saved."
    result = {"reason": reason, "recovery_cause": "control_observation", "blocker_type": "validation"}
    for source in ("a", "b"):
        if source == "b":
            add_run(service, "b", "j1", "waiting_browser", 5, packet=packet)
        with service.db.exclusive() as s:
            run = s.get(Run, source)
            run.reason = reason
            schedule_recovery(s, service.config, run, result)
    saved = service.db.get_setting("draft_recovery:j1")
    assert saved["scheduled"] is False and saved["status"] == "unchanged_failure"
    assert saved["blocker"].startswith("Recovery stopped: Required field incomplete: Country")


async def test_a_parked_model_outage_resumes_once_the_provider_is_back(service, monkeypatch):
    from jobpilot import provider_health
    from jobpilot.recovery import retry_due

    queued = []

    async def fake_queue(job_id, mode, *args, **kwargs):
        queued.append(job_id)
        return {"id": "q-" + job_id}

    async def nothing(*args, **kwargs):
        return None

    service.dispatch = nothing
    monkeypatch.setattr(service, "queue", fake_queue)
    service.db.set_setting("control", {"paused": False, "auto_submit": True})
    later = (datetime.now(UTC) + timedelta(minutes=25)).isoformat()
    for job_id in ("p1", "p2"):
        seed_job(service, job_id, "greenhouse")
        with service.db.exclusive() as s:
            s.get(Job, job_id).company = "Company " + job_id
        add_run(service, "src-" + job_id, job_id, "waiting_browser", 0, PROVIDER,
                {"match_approved": True, "profile": {"autonomous": True}})
        service.db.set_setting("draft_recovery:" + job_id, {
            "source_run_id": "src-" + job_id, "cause": "provider_unavailable", "scheduled": True, "attempts": 0,
            "not_before": later, "updated_at": datetime.now(UTC).isoformat()})
    # Cooling down: nothing starts.
    key = provider_health.key(service.config)
    service.db.set_setting(key, {"generation": 1, "failures": 2, "retry_at": 4e9})
    assert await retry_due(service) == [] and queued == []
    # The cooldown ended and no success yet: one run probes the provider with real work.
    service.db.set_setting(key, {"generation": 1, "failures": 2, "retry_at": 1.0})
    assert len(await retry_due(service)) == 1
    # A request succeeded after the cooldown: the rest resume without waiting out their timers.
    ticket = provider_health.acquire(service.db, service.config, 10)
    provider_health.finish(service.db, service.config, ticket)
    queued.clear()
    assert len(await retry_due(service)) == 2
