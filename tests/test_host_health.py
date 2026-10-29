"""Load shedding and the browser circuit (docs/forensics/FAILURE_ANALYSIS.md, 4.2).

From 2026-10-18 19:00 to 10-19 06:00 the host's event loop ran late: 940 field writes missed their deadline, 70 runs
timed out, and each timed-out run spent one of its job's three starts while the scheduler kept dispatching. On
2026-10-11/12 about 375 runs were dispatched into a worker Chrome that was gone.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from jobpilot import host_health, provider_health
from jobpilot.admission import QueueBlocked
from jobpilot.application_budget import HOST_OVERLOADED, nothing_sent
from jobpilot.db import Job, Run, Setting
from jobpilot.host_health import Gate, HostLoad


@pytest.fixture(autouse=True)
def clean_globals():
    host_health.DRAINING.clear()
    yield
    host_health.DRAINING.clear()


def fed(lags, step=0.5, start=1000.0):
    """A monitor fed one sample per `step` seconds; the clock stops at the last sample."""
    clock = {"t": start}
    load = HostLoad(wall=lambda: clock["t"])
    for lag in lags:
        clock["t"] += step
        load.record(lag)
    return load, clock


def iso(epoch):
    return datetime.fromtimestamp(epoch, UTC).isoformat()


# --- the event-loop monitor ----------------------------------------------------------------------------------------


def test_a_quiet_loop_is_ok_and_keeps_every_worker():
    load, _ = fed([0.01] * 60)
    assert load.current() == "ok" and load.limit(6) == 6


def test_a_loop_that_is_often_late_gives_up_one_worker():
    # One sample in six is 0.6 s late: the 95th percentile of the window is 0.6 s.
    load, _ = fed(([0.01] * 5 + [0.6]) * 10)
    assert load.current() == "loaded"
    assert load.limit(6) == 5 and load.limit(1) == 1


def test_a_loop_that_is_a_second_late_halves_the_workers_and_still_admits_work():
    """A limit of 1 left the autopilot no room (limit - occupied) while one run was active (2026-10-29)."""
    load, _ = fed(([0.01] * 5 + [1.4]) * 10)
    assert load.current() == "overloaded" and load.limit(6) == 3 and load.limit(1) == 1
    assert load.limit(6) - 1 > 0  # with one run active the autopilot still has room


def test_a_loop_that_is_seconds_late_runs_one_application_at_a_time():
    load, _ = fed([3.4] * 60, step=0.5)
    assert load.current() == "overloaded" and load.limit(6) == 1 and load.snapshot()["severe"] is True
    load, _ = fed([0.01, 0.01, 6.0])  # one stall beyond SEVERE_SECONDS is as deep
    assert load.limit(6) == 1


def test_a_brief_spike_does_not_hold_the_window_once_the_loop_is_back():
    """38 overloaded periods in 6 h, each one shutting the gate for the whole 30 s window (2026-10-29)."""
    load, clock = fed([0.01] * 40 + [1.5] * 6)
    assert load.current() == "overloaded"
    for _ in range(25):  # 12.5 s of a quiet loop: the spike is still inside the 30 s window
        clock["t"] += 0.5
        load.record(0.01)
    assert load.current() == "ok" and load.limit(6) == 6
    assert load.intervals[0][1] is not None


def test_a_sustained_overload_keeps_its_gate_in_both_windows():
    load, clock = fed([0.01] * 40 + [1.5] * 60)
    assert load.current() == "overloaded"
    for _ in range(10):  # the loop is still late ten seconds on
        clock["t"] += 0.5
        load.record(1.5)
    assert load.current() == "overloaded" and load.limit(6) == 3


def test_one_long_stall_counts_at_once_without_waiting_for_a_full_window():
    load, _ = fed([0.01, 0.01, 6.0])
    assert load.current() == "overloaded"


def test_too_few_samples_to_judge_is_unknown_and_does_not_limit():
    load, _ = fed([0.9] * 10)
    assert load.current() == "unknown" and load.limit(6) == 6


def test_a_spike_leaves_the_window_after_thirty_seconds():
    load, clock = fed([0.01] * 30 + [1.4] * 30)
    assert load.current() == "overloaded"
    for _ in range(80):
        clock["t"] += 0.5
        load.record(0.01)
    assert load.current() == "ok"


def test_a_monitor_that_stopped_reporting_says_nothing_about_the_host():
    load, clock = fed([1.4] * 60)
    assert load.current() == "overloaded"
    assert load.current(at=clock["t"] + host_health.STALE_SECONDS + 1) == "unknown"


def test_the_owner_can_switch_the_gate_off_and_retune_it():
    load, _ = fed([1.4] * 60)
    load.configure({"enabled": False})
    assert load.current() == "ok" and load.limit(6) == 6
    load.configure({"enabled": True, "overloaded_p95": 2.0, "loaded_p95": 1.0})
    assert load.current() == "loaded"
    load.configure({"overloaded_p95": "fast", "loaded_p95": -3})  # unusable values keep the defaults
    assert load.config["loaded_p95"] == host_health.LOADED_P95
    load.configure({"overloaded_p95": 0.2, "loaded_p95": 0.9})  # inverted thresholds keep the defaults
    assert load.config["overloaded_p95"] == host_health.OVERLOADED_P95


def test_overloaded_periods_are_remembered_and_measured_against_a_run():
    load, clock = fed([0.01] * 40)
    began = clock["t"]
    for _ in range(40):
        clock["t"] += 0.5
        load.record(1.5)
    for _ in range(100):
        clock["t"] += 0.5
        load.record(0.01)
    assert load.current() == "ok" and len(load.intervals) == 1 and load.intervals[0][1] is not None
    start, end = load.intervals[0]
    assert began <= start <= began + 12 and 20 < end - start < 60
    assert load.overloaded_seconds(0, clock["t"]) == pytest.approx(end - start)
    assert load.overloaded_seconds(0, start - 1) == 0
    assert load.overloaded_seconds(start + 5, end - 5) == pytest.approx(end - start - 10)


def test_a_long_stall_overloads_the_host_from_where_it_began():
    """A Mac that sleeps for ten minutes is one late sample on waking; the runs that timed out meanwhile must count."""
    load, clock = fed([0.01] * 40)
    clock["t"] += 600.0
    load.record(600.0)
    assert load.current() == "overloaded"
    began = load.intervals[0][0]
    assert began == pytest.approx(clock["t"] - 600.0)
    assert load.overloaded_seconds(clock["t"] - 600.0, clock["t"]) == pytest.approx(600.0)


async def test_the_watcher_samples_the_loop_and_survives_a_blocked_one():
    import time as clock

    load = HostLoad()
    task = asyncio.create_task(host_health.watch(None, interval=0.02, load=load))
    await asyncio.sleep(0.15)
    clock.sleep(0.3)  # block the loop: the next sample reports it
    await asyncio.sleep(0.1)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert len(load.samples) >= 5
    assert max(lag for _, lag in load.samples) >= 0.2


# --- the gate ------------------------------------------------------------------------------------------------------


def test_no_samples_no_circuit_no_drain_means_every_worker(service):
    assert host_health.check(6, db=service.db, load=HostLoad()) == Gate(6)


def test_a_drain_stops_everything(service):
    host_health.begin_drain()
    gate = host_health.check(6, db=service.db, load=HostLoad())
    assert gate.limit == 0 and gate.code == "draining"


def test_an_overloaded_host_limits_admission_but_never_blocks_it(service):
    load, _ = fed([1.5] * 60)
    gate = host_health.check(6, db=service.db, load=load, at=datetime.fromtimestamp(1030.0, UTC))
    assert gate.limit == 3 and gate.code == "host_overloaded"


def test_dispatch_limit_follows_the_host_and_keeps_the_model_cooldown_first(service, monkeypatch):
    load, _ = fed([0.6] * 60)
    monkeypatch.setattr(host_health, "monitor", load)
    monkeypatch.setattr(host_health.HostLoad, "current", lambda self, at=None: "loaded")
    workers = service.config.workers
    assert provider_health.dispatch_limit(service.db, service.config) == max(1, workers - 1)
    monkeypatch.setattr(
        provider_health, "selected", lambda db, config: {"answers": {"state": "cooldown"}}
    )
    gate = provider_health.dispatch_gate(service.db, service.config)
    assert gate.limit == 0 and gate.code == "provider_unavailable"
    assert "cooling down" in gate.reason


def test_the_browser_circuit_pauses_then_allows_one_probe(service):
    now = datetime(2026, 10, 20, 12, tzinfo=UTC)
    service.db.set_setting(
        "browser_outage",
        {"failures": 3, "last_at": now.isoformat(), "until": (now + timedelta(seconds=60)).isoformat()},
    )
    closed = host_health.check(6, db=service.db, load=HostLoad(), at=now + timedelta(seconds=10))
    assert closed.limit == 0 and closed.code == "browser_unavailable"
    probe = host_health.check(6, db=service.db, load=HostLoad(), at=now + timedelta(seconds=61))
    assert probe.limit == 1 and probe.code == "browser_probe"
    service.db.set_setting("browser_outage", {"failures": 2, "last_at": now.isoformat()})
    assert host_health.check(6, db=service.db, load=HostLoad(), at=now).limit == 6


def test_the_tightest_gate_wins(service):
    load, _ = fed([1.5] * 60)
    now = datetime.fromtimestamp(1030.0, UTC)
    service.db.set_setting(
        "browser_outage",
        {"failures": 3, "last_at": iso(1000), "until": iso(1010)},
    )
    gate = host_health.check(8, db=service.db, load=load, at=now)
    assert gate.limit == 1 and gate.code == "browser_probe"


async def test_a_queue_request_during_a_browser_outage_is_deferred_with_its_own_reason(service):
    now = datetime.now(UTC)
    service.db.set_setting(
        "browser_outage",
        {"failures": 4, "last_at": now.isoformat(), "until": (now + timedelta(minutes=5)).isoformat()},
    )
    with pytest.raises(QueueBlocked) as caught:
        await service._queue("no-such-job", "submit")
    assert caught.value.code == "browser_unavailable" and caught.value.outcome == "deferred"
    assert "model" not in str(caught.value)


# --- the browser circuit's bookkeeping -----------------------------------------------------------------------------

DEAD = "Open the ApplyPilot Chrome window: the browser is responding, but its automation connection is unavailable."
BROWSER_GONE = [
    "The local Chrome debugging endpoint could not be reached after a bounded retry. No employer form was opened.",
    "Chrome worker tab initialization failed at create_target (TimeoutError). No employer form was opened.",
]


def note(service, state, reason, started_at, at):
    with service.db.exclusive() as s:
        host_health.note_browser(s, state=state, reason=reason, started_at=started_at, at=at)
    return service.db.get_setting("browser_outage", {})


def test_three_runs_in_a_row_that_could_not_open_a_page_open_the_circuit_and_it_backs_off(service):
    at = datetime(2026, 10, 20, 12, tzinfo=UTC)
    assert "until" not in note(service, "timed_out", DEAD, None, at)
    assert "until" not in note(service, "timed_out", DEAD, None, at)
    third = note(service, "timed_out", DEAD, None, at)
    assert third["failures"] == 3 and datetime.fromisoformat(third["until"]) == at + timedelta(seconds=60)
    fourth = note(service, "timed_out", DEAD, None, at)
    assert datetime.fromisoformat(fourth["until"]) == at + timedelta(seconds=120)
    for _ in range(6):
        last = note(service, "timed_out", DEAD, None, at)
    assert datetime.fromisoformat(last["until"]) == at + timedelta(seconds=host_health.OUTAGE_MAX_SECONDS)


@pytest.mark.parametrize("reason", [DEAD, *BROWSER_GONE])
def test_every_browser_unavailable_text_counts_toward_the_circuit(reason):
    assert host_health.is_browser_outage("timed_out", reason)
    assert host_health.is_browser_outage("waiting_browser", reason)
    assert not host_health.is_browser_outage("confirmed", reason)


def test_a_run_that_reached_the_employer_after_the_last_failure_closes_the_circuit(service):
    at = datetime(2026, 10, 20, 12, tzinfo=UTC)
    for _ in range(4):
        note(service, "timed_out", DEAD, None, at)
    earlier = note(service, "confirmed", "Website confirmation captured", iso(at.timestamp() - 30), at)
    assert earlier["failures"] == 4  # it started before the last failure: no information
    paused = note(service, "cancelled", "Owner cancelled", iso(at.timestamp() + 30), at)
    assert paused["failures"] == 4  # the owner's hand says nothing about the browser
    closed = note(service, "waiting_answer", "Needs an answer", iso(at.timestamp() + 30), at)
    assert closed["failures"] == 0 and "until" not in closed


def test_an_ordinary_run_does_not_create_the_circuit_row(service):
    at = datetime(2026, 10, 20, 12, tzinfo=UTC)
    assert note(service, "confirmed", "Website confirmation captured", iso(at.timestamp()), at) == {}


# --- a deadline timeout while the host is overloaded costs no start -------------------------------------------------

TIMEOUT = {
    "state": "timed_out",
    "reason": "Application exceeded the 600-second execution budget",
    "recovery_cause": "operation_timeout",
}


def overloaded_for(seconds_ago_start, seconds_ago_end=0):
    load = HostLoad()
    now = load.wall()
    load.intervals = [[now - seconds_ago_start, None if not seconds_ago_end else now - seconds_ago_end]]
    return load, now


def test_a_timeout_inside_an_overload_window_becomes_a_free_retry():
    load, now = overloaded_for(500)
    freed = host_health.host_overloaded_result(
        TIMEOUT, run_state="running", started_at=iso(now - 600), load=load
    )
    assert freed["reason"].startswith(HOST_OVERLOADED) and nothing_sent(freed["state"], freed["reason"])
    assert freed["recovery_cause"] == "host_overloaded" and freed["blocker_type"] == "site_transient"
    assert freed["diagnostics"]["original_reason"] == TIMEOUT["reason"]
    assert 495 <= freed["diagnostics"]["overloaded_seconds"] <= 505
    assert TIMEOUT["reason"] != freed["reason"]  # the input is not modified


@pytest.mark.parametrize(
    "override, run_state",
    [
        ({}, "submitting"),  # a commit in flight stays protected
        ({"recovery_cause": "navigation_stalled"}, "running"),  # a stall on the page is not the host's fault
        ({"recovery_cause": ""}, "running"),
        ({"state": "submission_unknown"}, "running"),
        ({"state": "needs_review"}, "running"),
    ],
)
def test_only_a_pre_commit_clock_timeout_is_freed(override, run_state):
    load, now = overloaded_for(500)
    result = {**TIMEOUT, **override}
    assert host_health.host_overloaded_result(result, run_state=run_state, started_at=iso(now - 600), load=load) is None


def test_a_brief_overload_does_not_excuse_a_long_stuck_run():
    load, now = overloaded_for(100, 85)  # fifteen seconds of a 600 s run
    assert host_health.host_overloaded_result(TIMEOUT, run_state="running", started_at=iso(now - 600), load=load) is None
    load, now = overloaded_for(300)  # half of the run
    assert host_health.host_overloaded_result(TIMEOUT, run_state="running", started_at=iso(now - 600), load=load)


def test_a_run_that_timed_out_while_the_host_slept_is_excused():
    load, clock = fed([0.01] * 40)
    started = clock["t"] - 20  # the run began just before the stall
    clock["t"] += 900.0
    load.record(900.0)
    freed = host_health.host_overloaded_result(
        {**TIMEOUT, "reason": "Application exceeded the 900-second execution budget"},
        run_state="running",
        started_at=iso(started),
        ended_at=clock["t"],
        load=load,
    )
    assert freed and freed["recovery_cause"] == "host_overloaded"


def test_the_owner_switch_also_turns_the_free_timeout_off():
    load, now = overloaded_for(500)
    load.configure({"enabled": False})
    assert host_health.host_overloaded_result(TIMEOUT, run_state="running", started_at=iso(now - 600), load=load) is None


def seed_run(service, name, *, state="running", mode="submit", started_ago=600, reason=""):
    now = datetime.now(UTC)
    service.db.set_setting("application_budget", {"max_seconds": None})  # production runs without the lifetime cap
    with service.db.exclusive() as s:
        s.add(Job(id=name, identity=name, url=f"https://acme.wd1.myworkdayjobs.com/job/{name}", ats="workday",
                  company="Acme", title="Engineer", status="running"))
        s.add(Run(id=name + "-run", job_id=name, state=state, mode=mode, reason=reason,
                  started_at=(now - timedelta(seconds=started_ago)).isoformat(),
                  packet={"match_approved": True, "profile": {"autonomous": True}}))
    return name + "-run", name


def test_complete_stores_the_free_timeout_and_schedules_a_retry_without_a_repair_attempt(service, monkeypatch):
    load, _ = overloaded_for(500)
    monkeypatch.setattr(host_health, "monitor", load)
    run_id, job_id = seed_run(service, "free1")
    service.complete(run_id, dict(TIMEOUT), 600.0)
    with service.db.session() as s:
        run = s.get(Run, run_id)
        assert run.state == "timed_out" and run.reason.startswith(HOST_OVERLOADED)
        assert run.packet["recovery_cause"] == "host_overloaded"
        assert nothing_sent(run.state, run.reason)
    recovery = service.db.get_setting("draft_recovery:" + job_id)
    assert recovery["scheduled"] is True and recovery["cause"] == "host_overloaded"
    assert recovery.get("attempts", 0) == 0


def test_complete_leaves_a_timeout_on_a_healthy_host_alone(service, monkeypatch):
    monkeypatch.setattr(host_health, "monitor", HostLoad())
    run_id, _ = seed_run(service, "plain1")
    service.complete(run_id, dict(TIMEOUT), 600.0)
    with service.db.session() as s:
        run = s.get(Run, run_id)
        assert run.reason == TIMEOUT["reason"] and run.packet["recovery_cause"] == "operation_timeout"
        assert not nothing_sent(run.state, run.reason)


def test_complete_never_frees_a_run_that_had_started_its_commit(service, monkeypatch):
    load, _ = overloaded_for(500)
    monkeypatch.setattr(host_health, "monitor", load)
    run_id, _ = seed_run(service, "commit1", state="submitting")
    service.complete(run_id, dict(TIMEOUT), 600.0)
    with service.db.session() as s:
        run = s.get(Run, run_id)
        assert run.state == "submission_unknown" and not run.reason.startswith(HOST_OVERLOADED)


def test_complete_feeds_the_browser_circuit(service, monkeypatch):
    monkeypatch.setattr(host_health, "monitor", HostLoad())
    for i in range(3):
        run_id, _ = seed_run(service, f"dead{i}", started_ago=20)
        service.complete(run_id, {"state": "timed_out", "reason": DEAD, "recovery_cause": "browser_unavailable"}, 5.0)
    circuit = service.db.get_setting("browser_outage")
    assert circuit["failures"] == 3 and circuit["until"]
    run_id, _ = seed_run(service, "alive", started_ago=0)
    service.complete(run_id, {"state": "waiting_answer", "reason": "Needs an answer"}, 5.0)
    assert service.db.get_setting("browser_outage")["failures"] == 0


# --- the claim of a browser slot ------------------------------------------------------------------------------------


async def test_a_queued_run_waits_while_the_worker_chrome_is_gone(service, monkeypatch):
    from jobpilot.service import execution as service_module

    now = datetime.now(UTC)
    service.db.set_setting(
        "browser_outage",
        {"failures": 5, "last_at": now.isoformat(), "until": (now + timedelta(minutes=10)).isoformat()},
    )
    with service.db.exclusive() as s:
        s.add(Job(id="held", identity="held", url="https://careers.example.test/job/1", ats="oracle"))
        s.add(Run(id="held-run", job_id="held", mode="submit", state="queued", packet={}))
    slept = []

    async def stop_after_wait(seconds):
        slept.append(seconds)
        with service.db.exclusive() as s:
            row = s.get(Run, "held-run")
            assert row.state == "queued" and row.started_at is None
            row.state = "paused"  # End the fixture after proving nothing was claimed.

    monkeypatch.setattr(service_module, "asyncio", SimpleNamespace(sleep=stop_after_wait))
    await service._execute_owned("held-run")
    assert slept == [5]  # a closed gate polls slowly instead of every second


async def test_a_queued_run_stays_queued_when_the_server_is_stopping(service):
    with service.db.exclusive() as s:
        s.add(Job(id="stop", identity="stop", url="https://careers.example.test/job/2", ats="oracle"))
        s.add(Run(id="stop-run", job_id="stop", mode="submit", state="queued", packet={}))
    host_health.begin_drain()
    await asyncio.wait_for(service._execute_owned("stop-run"), timeout=5)
    with service.db.session() as s:
        run = s.get(Run, "stop-run")
        assert run.state == "queued" and run.started_at is None


def test_runtime_health_reports_the_host_gates(client, service):
    host_health.begin_drain()
    body = client.get("/api/runtime-health").json()
    host = body["host"]
    assert host["draining"] is True and host["state"] in {"unknown", "ok", "loaded", "overloaded"}
    assert host["browser_failures_in_a_row"] == 0 and host["browser_paused_until"] is None
    now = datetime.now(UTC)
    service.db.set_setting(
        "browser_outage", {"failures": 3, "last_at": now.isoformat(), "until": (now + timedelta(minutes=1)).isoformat()}
    )
    host = client.get("/api/runtime-health").json()["host"]
    assert host["browser_failures_in_a_row"] == 3 and host["browser_paused_until"]


# --- a loop so late that it takes few samples (2026-10-29) ----------------------------------------------------------


def test_a_loop_so_late_that_it_takes_few_samples_is_overloaded_not_unknown():
    """Every timer fired 2.5 s late, so a 30 s window held 10 samples, under MIN_SAMPLES: it read "unknown" and the
    gate let every worker start while the host was at its worst."""
    load, _ = fed([2.5] * 40, step=3.0)
    assert len(load.samples) < host_health.MIN_SAMPLES
    assert load.current() == "overloaded" and load.limit(6) == 3


def test_sparse_samples_of_a_barely_late_loop_are_loaded_not_overloaded():
    load, _ = fed([0.8] * 30, step=1.3)  # 23 samples of 30 s, then fewer as the window slides: gap 1.3 s, late 0.8 s
    assert load.current() == "loaded"


def test_a_new_monitor_still_waits_before_judging():
    """Fewer samples than MIN_SAMPLES in a monitor younger than ten seconds is still too little to judge."""
    load, _ = fed([0.9] * 8, step=1.0)
    assert load.current() == "unknown" and load.limit(6) == 6


def test_a_quiet_window_after_a_silent_gap_is_not_blamed_on_the_gap():
    load, clock = fed([0.01] * 60)
    clock["t"] += 31.0  # nothing reported for a window, then the loop resumes at its normal pace
    for _ in range(5):
        clock["t"] += 0.5
        load.record(0.01)
    assert load.current() == "ok"  # judged by the pace since the gap, not by the gap
