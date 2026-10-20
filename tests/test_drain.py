"""A stop (a deploy) never cuts a commit (docs/INFRA.md, "Stopping and deploying").

Every `docker compose up/restart/stop` sends SIGTERM. Before this, the app simply exited: a run in its commit became
"Worker interrupted during submit; reconcile before retrying" (submission_unknown, never retried blindly), and 139 runs
were interrupted by deploys in the October history, 35 of them in 23 minutes on 10-18.
"""

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from jobpilot import host_health
from jobpilot.application_budget import nothing_sent
from jobpilot.browser import BrowserEngine
from jobpilot.db import Budget, Job, Run
from jobpilot.host_health import INTERRUPTED, ServerDraining, refused_commit_result


@pytest.fixture(autouse=True)
def clean_drain(monkeypatch):
    host_health.DRAINING.clear()
    monkeypatch.setattr(host_health, "DRAIN_POLL_SECONDS", 0.05)
    yield
    host_health.DRAINING.clear()


def seed(service, name, state="running", demo=False):
    service.db.set_setting("application_budget", {"max_seconds": None})
    service.db.set_setting("control", {"paused": False, "auto_submit": True})
    with service.db.exclusive() as s:
        s.add(Job(id=name, identity=name, url=f"https://careers.example.test/{name}", ats="oracle", demo=demo,
                  company="Acme", title="Engineer", status="running"))
        s.add(Run(id=name + "-run", job_id=name, mode="submit", state=state,
                  started_at=datetime.now(UTC).isoformat(),
                  packet={"match_approved": True, "profile": {"autonomous": True}}))
    return name + "-run"


# --- no commit begins during a drain -------------------------------------------------------------------------------


def test_the_reservation_is_granted_before_a_drain_and_refused_during_one(service):
    first = seed(service, "before")
    service.reserve_submission(first)
    with service.db.session() as s:
        assert s.get(Run, first).state == "submitting"
    second = seed(service, "during")
    host_health.begin_drain()
    with pytest.raises(ServerDraining):
        service.reserve_submission(second)
    with service.db.session() as s:
        assert s.get(Run, second).state == "running"  # nothing was reserved
        day = datetime.now(ZoneInfo(service.config.timezone)).date().isoformat()
        assert s.get(Budget, day).submissions == 1  # only the first reservation spent the day's cap


def test_a_commit_already_reserved_may_retry_its_validation_during_a_drain(service):
    run_id = seed(service, "reserved", state="submitting")
    host_health.begin_drain()
    service.authorize_validation_retry(run_id, {"category": "validation", "missing_fields": ["Major"]})


def test_a_refused_commit_is_a_free_interruption_that_resumes_after_the_restart(service):
    result = refused_commit_result(ServerDraining())
    assert result == {"state": "waiting_browser", "reason": INTERRUPTED, "recovery_cause": "worker_interrupted"}
    assert nothing_sent(result["state"], result["reason"])
    run_id = seed(service, "freed")
    service.complete(run_id, result, 40.0)
    recovery = service.db.get_setting("draft_recovery:freed")
    assert recovery["scheduled"] is True and recovery["cause"] == "worker_interrupted"
    assert recovery["not_before"] <= datetime.now(UTC).isoformat()


@pytest.mark.parametrize(
    "error, cause",
    [
        (ValueError("Workers were paused before submission"), "control_observation"),
        (ValueError("Daily submission cap reached"), "control_observation"),
    ],
)
def test_every_other_refusal_keeps_its_old_meaning(error, cause):
    result = refused_commit_result(error)
    assert result["recovery_cause"] == cause and result["reason"] == str(error)


async def test_both_engine_paths_turn_a_stop_into_that_result():
    """The generic engine's _finish and the adapters' commit both reserve through BrowserEngine.try_reserve_commit."""
    calls = []

    def reserve(run_id):
        calls.append(run_id)
        if host_health.draining():
            raise ServerDraining()

    engine = object.__new__(BrowserEngine)
    engine.service, engine.run_id = SimpleNamespace(reserve_submission=reserve), "r1"
    assert await engine.try_reserve_commit() is None
    host_health.begin_drain()
    refused = await engine.try_reserve_commit()
    assert refused["reason"] == INTERRUPTED and refused["recovery_cause"] == "worker_interrupted"
    assert calls == ["r1", "r1"]


def test_the_adapter_commit_and_the_generic_engine_share_the_helper():
    import inspect

    from jobpilot.adapters.base import Adapter

    assert "try_reserve_commit" in inspect.getsource(Adapter.commit)
    assert "try_reserve_commit" in inspect.getsource(BrowserEngine._finish)


# --- the drain itself -----------------------------------------------------------------------------------------------


async def test_an_idle_server_drains_at_once_and_stops_new_work(service):
    assert await service.drain(30) == []
    assert host_health.draining()
    assert service.db.get_setting("last_drain")["still_committing"] == []
    gate = host_health.check(6, db=service.db, load=host_health.HostLoad())
    assert gate.limit == 0 and gate.code == "draining"


async def test_the_drain_waits_for_a_commit_to_finish(service):
    run_id = seed(service, "slow", state="submitting")

    async def finish_later():
        await asyncio.sleep(0.4)
        service.complete(run_id, {"state": "confirmed", "reason": "Website confirmation captured", "receipt": {}}, 5)

    task = asyncio.create_task(finish_later())
    left = await service.drain(10)
    await task
    assert left == []
    waited = service.db.get_setting("last_drain")["waited_seconds"]
    assert waited <= 2


async def test_the_drain_gives_up_after_the_grace_and_names_the_runs(service):
    run_id = seed(service, "stuck", state="submitting")
    left = await service.drain(0.3)
    assert left == [run_id]
    assert service.db.get_setting("last_drain")["still_committing"] == [run_id]


async def test_runs_that_are_only_filling_a_form_do_not_hold_the_drain(service):
    seed(service, "filling", state="running")
    assert await asyncio.wait_for(service.drain(30), timeout=5) == []


# --- the app's own shutdown goes through it --------------------------------------------------------------------------


def test_the_apps_shutdown_drains_before_it_closes_anything(config, monkeypatch):
    from fastapi.testclient import TestClient

    import jobpilot.workers as workers
    from jobpilot.api import create_app

    calls = []

    async def start(service):
        calls.append("start")

    async def monitor(service):
        await asyncio.sleep(3600)

    monkeypatch.setattr(workers, "start", start)
    monkeypatch.setattr(workers, "monitor_scheduler", monitor)
    monkeypatch.setattr(workers, "stop", lambda: calls.append("stop"))
    config.enable_workers = True
    app = create_app(config)

    async def drain(grace):
        calls.append(("drain", grace))
        return []

    monkeypatch.setattr(app.state.service, "drain", drain)
    with TestClient(app):
        pass
    assert calls == ["start", ("drain", config.shutdown_drain_seconds), "stop"]
    assert config.shutdown_drain_seconds == 150


def test_the_container_waits_longer_than_the_drain():
    from pathlib import Path

    import yaml

    from jobpilot.config import Settings

    compose = yaml.safe_load((Path(__file__).resolve().parent.parent / "compose.yaml").read_text())
    grace = compose["services"]["app"]["stop_grace_period"]
    seconds = int(str(grace).rstrip("s"))
    assert seconds >= Settings.model_fields["shutdown_drain_seconds"].default + 30
