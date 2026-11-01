"""The one Stop: nothing new starts from any path, running applications return to the queue, a commit is never cut, and only
Resume lifts it (owner, 2026-11-01: "could not stop applications"). These hold the rules in place; the pass/fail proof is a real
run against a local site, through the real button."""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from jobpilot import outbox as outbox_module
from jobpilot import stop
from jobpilot.admission import QueueBlocked
from jobpilot.application_budget import allowance, nothing_sent
from jobpilot.db import Job, Outbox, Resume, Run, Setting, now
from jobpilot.host_health import refused_commit_result
from jobpilot.job_control import _run_and_control, manage, watch
from jobpilot.native import supervisor
from jobpilot.outbox import HELD_BY_STOP, claim, enqueue


def seed_job(service, name="acme", status="ready", **job):
    service.db.set_setting("control", {"paused": False, "auto_submit": True})
    with service.db.exclusive() as s:
        s.add(Job(id=name, identity=name, url=f"https://careers.example.test/{name}", ats="greenhouse", company=name.title(),
                  title="Engineer", status=status, score=90, **job))
    return name


def seed_run(service, name="acme", state="running", job_status=None, **packet):
    seed_job(service, name, status=job_status or state)
    with service.db.exclusive() as s:
        s.add(Run(id=name + "-run", job_id=name, mode="submit", state=state, started_at=now(), created_at=now(),
                  packet={"match_approved": True, "profile": {"autonomous": True}, "worker_slot": 1, **packet}))
    return name + "-run"


def add_resume(service):
    with service.db.exclusive() as s:
        s.add(Resume(id="r1", name="R", filename="r.pdf", sha256="abc", text="x"))


# --- nothing new starts ---------------------------------------------------------------------------------------------------


def test_admission_refuses_during_a_stop_and_writes_no_run(service):
    job = seed_job(service)
    stop.press(service.db)
    with pytest.raises(QueueBlocked) as refused:
        service._admit(job, "submit", owner_pick=True)
    assert refused.value.code == "stopped" and refused.value.outcome == "deferred"
    with service.db.session() as s:
        assert s.query(Run).count() == 0
        assert s.get(Setting, "owner_apply_pick:" + job) is None  # A refused click leaves nothing behind.


def test_the_claim_leaves_a_queued_run_queued(service):
    run_id = seed_run(service, state="queued")
    stop.press(service.db)
    asyncio.run(service._execute_owned(run_id))
    with service.db.session() as s:
        run = s.get(Run, run_id)
        assert run.state == "queued"


def test_no_commit_can_begin_after_stop_and_refusing_one_is_a_return_to_the_queue(service):
    run_id = seed_run(service)
    stop.press(service.db)
    with pytest.raises(stop.Stopped) as refused:
        service.reserve_submission(run_id)
    with service.db.session() as s:
        assert s.get(Run, run_id).state == "running"
    assert refused_commit_result(refused.value)["state"] == "stopped"


def test_a_repaired_resubmit_is_a_new_commit_and_a_stop_refuses_it(service):
    run_id = seed_run(service, state="submitting")
    stop.press(service.db)
    rejection = {"type": "explicit_validation_rejection", "category": "validation", "missing_fields": ["Phone"]}
    with pytest.raises(stop.Stopped):
        service.authorize_validation_retry(run_id, rejection)


def test_a_retry_during_a_stop_leaves_the_job_as_it_was(service):
    run_id = seed_run(service, state="waiting_browser")
    stop.press(service.db)
    with pytest.raises(QueueBlocked):
        asyncio.run(manage(service, "acme", "resume", actor="repair"))
    with service.db.session() as s:
        assert s.get(Job, "acme").status == "waiting_browser" and s.get(Setting, "job_control:acme") is None
        assert s.get(Run, run_id).state == "waiting_browser"


def test_starting_a_batch_does_not_lift_a_stop(service):
    from jobpilot.autopilot import start_autonomous

    service.dispatch = lambda run_id: None
    service.db.set_setting("control", {"paused": False, "auto_submit": False})
    stop.press(service.db)
    with pytest.raises(ValueError, match="Press Resume"):
        asyncio.run(start_autonomous(service, 5))
    assert service.db.get_setting("control")["paused"] is True


def test_a_stale_settings_write_cannot_lift_a_stop(client):
    client.put("/api/control", json={"auto_submit": True})
    assert client.post("/api/stop", json={}).json()["stopped"] is True
    answer = client.put("/api/control", json={"auto_submit": False})
    assert answer.json() == {"paused": True, "auto_submit": False}


def test_resume_is_its_own_press_and_keeps_the_other_switch(client):
    client.put("/api/control", json={"auto_submit": True})
    client.post("/api/stop", json={})
    assert client.post("/api/resume", json={}).json()["stopped"] is False
    assert client.get("/api/control").json() == {"paused": False, "auto_submit": True}


# --- deliveries that lead to a start ---------------------------------------------------------------------------------------


def test_held_deliveries_stay_unclaimed_and_unspent_and_messages_still_go(service):
    with service.db.exclusive() as s:
        for kind in HELD_BY_STOP:
            enqueue(s, kind, kind, {})
        enqueue(s, "notify", "n", {}, run_id="r")
    stop.press(service.db)
    assert claim(service.db)["kind"] == "notify"
    assert claim(service.db) is None
    assert claim(service.db, ["mail_resume", "fact_resume", "dispatch", "refill", "tailor"]) is None
    with service.db.session() as s:
        assert {r.kind: r.attempts for r in s.query(Outbox).filter(Outbox.kind != "notify")} == dict.fromkeys(HELD_BY_STOP, 0)
    stop.lift(service.db)
    assert claim(service.db)["kind"] in HELD_BY_STOP


# --- a running application stops, and it isn't a failure ---------------------------------------------------------------------


class Engine(SimpleNamespace):
    armed = False
    result = None


def watched(service, run_id, *, engine=None, seconds=1.5):
    """Run `watch` over a task that stays busy for `seconds`. Returns (engine, whether it was cancelled)."""
    engine = engine or Engine()

    async def go():
        async def work():
            await asyncio.sleep(seconds)

        execution = asyncio.create_task(work())
        monitor = asyncio.create_task(watch(service, engine, execution, run_id))
        try:
            await execution
            return False
        except asyncio.CancelledError:
            return True
        finally:
            monitor.cancel()
            await asyncio.gather(monitor, return_exceptions=True)

    return engine, asyncio.run(go())


def test_stop_cancels_a_running_application_within_a_poll(service):
    run_id = seed_run(service)
    stop.press(service.db)
    engine, cancelled = watched(service, run_id)
    assert cancelled and engine.result["state"] == "stopped" and engine.result["reason"].startswith(stop.STOPPED)


def test_stop_never_cuts_a_commit(service):
    run_id = seed_run(service, state="submitting")
    stop.press(service.db)
    engine, cancelled = watched(service, run_id)
    assert not cancelled and engine.result is None


def test_stop_never_cuts_an_armed_engine(service):
    run_id = seed_run(service)
    stop.press(service.db)
    _, cancelled = watched(service, run_id, engine=Engine(armed=True))
    assert not cancelled


def test_the_owners_word_is_read_before_the_runs_state(service, monkeypatch):
    """A commit that begins between the two reads must be seen as a commit: reading the state first cut it as if it had not."""
    run_id = seed_run(service)
    real = stop.stopped_in

    def press_and_commit_during_the_read(session):
        answer = real(session)
        service.db.set_setting("control", {"paused": True, "auto_submit": True})
        with service.db.exclusive() as s:
            s.get(Run, run_id).state = "submitting"
        return answer

    monkeypatch.setattr(stop, "stopped_in", press_and_commit_during_the_read)
    state, _, everything = _run_and_control(service.db, run_id)
    assert state == "submitting" and everything is False


def test_a_stopped_run_goes_back_to_the_queue_with_nothing_on_the_record(service):
    run_id = seed_run(service, job_status="running")
    service.config.failure_reports_enabled = True
    assert service.return_to_queue(run_id, {**stop.stopped_result(), "browser_checkpoint": {"resume_sha": "x"}}, 3.0)
    with service.db.session() as s:
        run = s.get(Run, run_id)
        assert (run.state, run.started_at, run.finished_at, run.reason) == ("queued", None, None, "")
        assert run.packet["browser_checkpoint"] == {"resume_sha": "x"}
        assert s.get(Job, "acme").status == "queued"
        assert s.query(Outbox).count() == 0  # No report, no repair, no message, no refill.
        assert s.get(Setting, "draft_recovery:acme") is None
        assert allowance(s, "acme")[2] == 0  # It spent no start.


def test_a_commit_is_never_returned_to_the_queue(service):
    run_id = seed_run(service, state="submitting")
    assert service.return_to_queue(run_id, stop.stopped_result(), 1.0) is False
    with service.db.session() as s:
        assert s.get(Run, run_id).state == "submitting"


def test_a_job_removed_meanwhile_is_not_returned_to_the_queue(service):
    run_id = seed_run(service)
    asyncio.run(manage(service, "acme", "delete"))
    assert service.return_to_queue(run_id, stop.stopped_result(), 1.0) is False


def test_a_refused_repaired_commit_costs_no_start():
    assert nothing_sent("waiting_browser", stop.STOPPED)


# --- a removed job --------------------------------------------------------------------------------------------------------


def test_a_removed_job_is_cancelled_and_its_last_run_schedules_nothing(service):
    run_id = seed_run(service)
    asyncio.run(manage(service, "acme", "delete"))
    engine, cancelled = watched(service, run_id)
    assert cancelled and engine.result["state"] == "cancelled"
    service.complete(run_id, {"state": "waiting_browser", "reason": "Page refused", "recovery_cause": "control_observation"}, 1.0)
    with service.db.session() as s:
        assert s.get(Job, "acme").status == "archived"
        recovery = s.get(Setting, "draft_recovery:acme")
        assert recovery is None or not recovery.value.get("scheduled")
        assert not s.query(Outbox).filter(Outbox.kind == "repair").count()


def test_a_removed_job_stays_out_of_the_queue(service):
    seed_job(service)
    asyncio.run(manage(service, "acme", "delete"))
    with pytest.raises(QueueBlocked):
        service._admit("acme", "submit", owner_pick=True)


# --- what it says ---------------------------------------------------------------------------------------------------------


def test_the_sentence_says_what_is_stopped_and_finishing_with_a_countdown(service):
    seed_run(service, "finishing", state="submitting")
    seed_run(service, "closing", state="running")
    seed_run(service, "waiting", state="queued")
    service.db.set_setting("control", {"paused": False, "auto_submit": True})
    stop.press(service.db)
    answer = stop.status(service.db)
    assert answer["stopped"] and not answer["settled"]
    assert "1 application in the final submit step will finish (Finishing), up to " in answer["sentence"]
    assert "1 application closing, about 0:1" in answer["sentence"]
    assert "1 application waits for Resume" in answer["sentence"]


def test_it_says_when_everything_has_stopped(service):
    seed_run(service, "waiting", state="queued")
    stop.press(service.db)
    answer = stop.status(service.db)
    assert answer["settled"] and answer["sentence"].startswith("Stopped. Nothing is applying")
    assert answer["queued"] == 1


def test_it_says_nothing_when_nothing_is_stopped(service):
    seed_run(service, "busy", state="running")
    answer = stop.status(service.db)
    assert not answer["stopped"] and not answer["settled"] and answer["sentence"] == "" and answer["running"] == 1


def test_a_second_press_keeps_the_first_clock(service):
    service.db.set_setting("control", {"paused": False, "auto_submit": True})
    assert stop.press(service.db) is True
    first = service.db.get_setting("control")["stopped_at"]
    assert stop.press(service.db) is False
    assert service.db.get_setting("control")["stopped_at"] == first


def test_a_stop_survives_a_restart_because_it_is_a_row(config, service):
    from jobpilot.db import Database

    stop.press(service.db)
    assert stop.stopped(Database(config.database_url)) is True


# --- quit -----------------------------------------------------------------------------------------------------------------


def test_queued_work_during_a_stop_is_not_in_progress():
    assert "state = 'queued' and not" in supervisor.ACTIVE_RUNS and "'paused' = 'true'" in supervisor.ACTIVE_RUNS
