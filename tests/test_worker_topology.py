"""Four ATS workers, one specialist and one custom-site worker by default (owner 2026-10-28), configurable."""

import pytest

from jobpilot.application_slots import claim_slot
from jobpilot.config import Settings, Topology
from jobpilot.db import Job, Run
from jobpilot.pipeline_comparison import (
    NORMAL,
    SPECIALIST,
    as_topology,
    pool_capacity,
    pool_slot_ids,
    slots,
    snapshot,
)


def settings(**values):
    return Settings(_env_file=None, **values)


def test_default_is_four_ats_workers_one_specialist_and_the_custom_site_worker():
    config = settings()
    assert (config.normal_workers, config.specialist_workers, config.workers) == (4, 1, 6)
    assert pool_slot_ids(NORMAL, config.topology) == [1, 2, 3, 4]
    assert pool_slot_ids(SPECIALIST, config.topology) == [5]
    assert pool_slot_ids("custom", config.topology) == [6]


def test_the_split_is_configurable_through_the_environment(monkeypatch):
    monkeypatch.setenv("NORMAL_WORKERS", "4")
    monkeypatch.setenv("SPECIALIST_WORKERS", "1")
    config = settings(custom_ats_worker=False)
    assert config.workers == 5
    assert pool_slot_ids(NORMAL, config.topology) == [1, 2, 3, 4]
    assert pool_slot_ids(SPECIALIST, config.topology) == [5]


def test_specialists_can_be_switched_off():
    config = settings(specialist_workers=0, normal_workers=6, custom_ats_worker=False)
    assert pool_slot_ids(SPECIALIST, config.topology) == []
    assert config.workers == 6


def test_the_fleet_has_a_hard_cap():
    with pytest.raises(ValueError, match="may not exceed"):
        settings(normal_workers=30, specialist_workers=4)


def test_older_callers_may_still_pass_a_plain_total():
    assert as_topology(8) == Topology(6, 2)
    assert as_topology(Topology(3, 1)) == Topology(3, 1)
    assert pool_slot_ids(SPECIALIST, 20) == [19, 20]


def test_the_dashboard_snapshot_follows_the_configured_split(service):
    view = snapshot(service.db, Topology(4, 1))
    assert (view["normal_workers"], view["specialist_workers"]) == (4, 1)
    assert [(w["slot"], w["lane"]) for w in view["assignments"]] == [
        (1, NORMAL),
        (2, NORMAL),
        (3, NORMAL),
        (4, NORMAL),
        (5, SPECIALIST),
    ]


def add_run(service, name, pool, slot, state="running"):
    with service.db.session() as s:
        s.add(
            Job(
                id=name,
                identity=name,
                url=f"https://{name}.example.test/jobs/1",
                ats="workday",
                status=state,
                company=name,
                title="Engineer",
            )
        )
        s.add(
            Run(
                id=name + "-run",
                job_id=name,
                state=state,
                mode="submit",
                packet={"worker_pool": pool, "worker_slot": slot},
            )
        )


def test_each_pool_has_its_own_capacity(service):
    add_run(service, "a", SPECIALIST, 7)
    add_run(service, "b", SPECIALIST, 8)
    with service.db.session() as s:
        assert pool_capacity(s, SPECIALIST, Topology(6, 2)).available == 0
        assert pool_capacity(s, NORMAL, Topology(6, 2)).available == 6
        # A third specialist slot makes room; the ATS pool is unaffected.
        assert pool_capacity(s, SPECIALIST, Topology(6, 3)).available == 1
    add_run(service, "c", SPECIALIST, None, state="queued")
    with service.db.session() as s:
        queued = s.get(Run, "c-run")
        assert slots(s, queued, Topology(6, 2)) == []
        assert slots(s, queued, Topology(6, 3)) == [9]


def test_a_specialist_run_claims_a_specialist_slot(service):
    add_run(service, "d", SPECIALIST, None, state="queued")
    add_run(service, "e", NORMAL, None, state="queued")
    with service.db.exclusive() as s:
        assert claim_slot(s, s.get(Run, "d-run"), Topology(6, 2)) == 7
        assert claim_slot(s, s.get(Run, "e-run"), Topology(6, 2)) == 1


def test_a_specialist_run_cannot_start_when_there_are_no_specialists(service):
    add_run(service, "f", SPECIALIST, None, state="queued")
    with service.db.exclusive() as s, pytest.raises(RuntimeError, match="No application-owner slot"):
        claim_slot(s, s.get(Run, "f-run"), Topology(6, 0))


def test_capacity_reads_only_the_packet_keys_it_decides_on(service):
    """Every poll, autopilot tick and Apply click decoded each active run's whole ~320 KB packet (2026-10-22)."""
    from jobpilot.admission import active_runs, capacity

    add_run(service, "g", SPECIALIST, 7)
    add_run(service, "h", NORMAL, None, state="queued")
    with service.db.exclusive() as s:
        run = s.get(Run, "g-run")
        run.packet = {**run.packet, "verification_only": True, "browser_trace": "x" * 300_000}
    with service.db.session() as s:
        rows = {run.id: (run, job) for run, job in active_runs(s)}
        assert rows["g-run"][0].packet == {"worker_pool": SPECIALIST, "worker_slot": 7, "verification_only": True}
        assert rows["h-run"][0].packet == {"worker_pool": NORMAL}
        assert rows["g-run"][1].ats == "workday" and not hasattr(rows["g-run"][1], "description")
        assert (capacity(s, 8).running, pool_capacity(s, SPECIALIST, Topology(6, 2)).available) == (1, 1)
    workers = snapshot(service.db, Topology(6, 2))
    assert [w["run_id"] for w in workers["assignments"] if w["run_id"]] == ["g-run"]
