"""Finished DBOS workflows past the retention age go, on DBOS's own routine; work in flight is never touched."""

import time

import pytest
from dbos._schemas.system_database import SystemSchema
from sqlalchemy import select

from jobpilot import retention
from jobpilot.cli import prune_workflows
from jobpilot.config import Settings


@pytest.fixture
def system_database(tmp_path):
    config = Settings(_env_file=None, data_dir=tmp_path, app_token="t").prepare()
    database = retention.open_system_database(config)
    database.run_migrations()
    yield database
    database.destroy()


def add_workflow(database, uuid, status, completed_at, created_at=1):
    table = SystemSchema.workflow_status
    with database.engine.begin() as c:
        c.execute(table.insert().values(workflow_uuid=uuid, status=status, name="w", completed_at=completed_at,
                                        created_at=created_at, updated_at=created_at))


def remaining(database):
    table = SystemSchema.workflow_status
    with database.engine.begin() as c:
        return set(c.execute(select(table.c.workflow_uuid)).scalars())


def test_retention_deletes_only_finished_workflows_past_the_cutoff(system_database):
    now_s = time.time()
    old, recent = int((now_s - 20 * 86400) * 1000), int((now_s - 2 * 86400) * 1000)
    add_workflow(system_database, "old-success", "SUCCESS", old)
    add_workflow(system_database, "old-cancelled", "CANCELLED", old - 86_400_000 * 5)
    add_workflow(system_database, "recent-success", "SUCCESS", recent)
    add_workflow(system_database, "old-pending", "PENDING", None)
    add_workflow(system_database, "old-enqueued", "ENQUEUED", None)
    add_workflow(system_database, "old-delayed", "DELAYED", None)
    counted = retention.prune(system_database, 14, apply=False, at=now_s)
    assert counted["eligible"] == 2 and counted["deleted"] == 0 and counted["in_flight"] == 3
    assert len(remaining(system_database)) == 6, "counting deletes nothing"
    done = retention.prune(system_database, 14, at=now_s)
    assert done["deleted"] == 2 and done["applied"]
    assert remaining(system_database) == {"recent-success", "old-pending", "old-enqueued", "old-delayed"}
    assert retention.prune(system_database, 14, at=now_s)["deleted"] == 0


def test_retention_stops_between_days(system_database):
    now_s = time.time()
    for days in range(20, 40):
        add_workflow(system_database, f"w{days}", "SUCCESS", int((now_s - days * 86400) * 1000))
    report = retention.prune(system_database, 14, stop=lambda: True, at=now_s)
    assert report["stopped"] and 0 < report["deleted"] < 20


def test_retention_is_a_setting_with_the_asked_default(tmp_path):
    assert Settings(_env_file=None, data_dir=tmp_path).dbos_retention_days == 14


async def test_the_schedule_records_each_round_and_survives_a_failure(service, monkeypatch):
    """One failed round is written down and the next day's still runs."""
    import asyncio
    from types import SimpleNamespace

    import dbos._dbos as dbos_module

    rounds = []

    def fake_prune(database, days, stop=None):
        rounds.append(days)
        if len(rounds) == 1:
            raise OSError("database away")
        return {"days": days, "deleted": 5}

    async def no_wait(_):
        if len(rounds) >= 2:
            raise asyncio.CancelledError

    monkeypatch.setattr(retention, "prune", fake_prune)
    monkeypatch.setattr(retention.asyncio, "sleep", no_wait)
    monkeypatch.setattr(dbos_module, "_get_dbos_instance", lambda: SimpleNamespace(_sys_db=object()))
    with pytest.raises(asyncio.CancelledError):
        await retention.watch(service)
    assert rounds == [14, 14]
    saved = service.db.get_setting(retention.SETTING)
    assert saved["state"] == "healthy" and saved["deleted"] == 5


def test_the_command_counts_unless_told_and_refuses_no_age(tmp_path):
    config = Settings(_env_file=None, data_dir=tmp_path, app_token="t").prepare()
    said = []
    assert prune_workflows(config, 14, True, say=said.append) == 0
    assert said == ["No DBOS history yet."], "an installation whose workers never started has nothing to prune"
    database = retention.open_system_database(config)
    database.run_migrations()
    database.destroy()
    assert prune_workflows(config, 14, False, say=said.append) == 0
    assert '"applied": false' in said[-1]
    config.dbos_retention_days = 0
    assert prune_workflows(config, None, True, say=said.append) == 1
