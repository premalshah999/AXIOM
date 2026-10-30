"""DBOS retention: finished workflows older than DBOS_RETENTION_DAYS are deleted, daily.

Nothing else ever removes them. On 2026-10-30 `workflow_status` held 556,000 rows: 360,000 source sweeps, 153,000 autopilot
ticks and 31,000 scheduled ticks, with their inputs, outputs and steps, 845 MB in all. No code reads a finished workflow:
business state lives in `runs`, and deduplication only considers workflows still in flight.

DBOS's own retention does the deleting (the routine its hosted conductor calls): it selects on `completed_at`, which is
NULL until a workflow reaches a terminal state, so a running, queued or delayed one can never match. It also takes the
retention lock, so two rounds never overlap. Each step covers one day of history so that a stop waits for one short step.
"""

import asyncio
import logging
import time
from types import SimpleNamespace

import sqlalchemy as sa

from .db import now

log = logging.getLogger("jobpilot.retention")
DAY_MS = 86_400_000
BATCH = 2000
SETTING = "dbos_retention"
FIRST_DELAY_SECONDS = 900  # Never part of a start's load.
INTERVAL_SECONDS = 86_400


def open_system_database(config):
    """DBOS's system database for a process where DBOS is not running (a command). Close with .destroy()."""
    from dbos._serialization import DefaultSerializer
    from dbos._sys_db import SystemDatabase

    return SystemDatabase.create(
        system_database_url=config.dbos_database_url,
        engine_kwargs={"pool_size": 2, "max_overflow": 0, "pool_pre_ping": True, "pool_timeout": 30},
        engine=None,
        schema=None if config.dbos_database_url.startswith("sqlite") else "dbos",
        serializer=DefaultSerializer(),
        executor_id=None,
        use_listen_notify=False,
    )


def has_history(system_database) -> bool:
    """False on an installation whose workers have never started: DBOS creates its tables then."""
    schema = None if system_database.engine.dialect.name == "sqlite" else "dbos"
    return sa.inspect(system_database.engine).has_table("workflow_status", schema=schema)


def counts(system_database, cutoff) -> dict:
    """Finished workflows older than the cutoff, the oldest's completion time, and those still in flight."""
    from dbos._schemas.system_database import SystemSchema

    table = SystemSchema.workflow_status
    with system_database.engine.begin() as c:
        eligible, oldest = c.execute(
            sa.select(sa.func.count(), sa.func.min(table.c.completed_at)).where(table.c.completed_at < cutoff)
        ).one()
        in_flight = c.execute(sa.select(sa.func.count()).where(table.c.completed_at.is_(None))).scalar()
    return {"eligible": eligible, "oldest": oldest, "in_flight": in_flight}


def prune(system_database, days, *, apply=True, stop=None, at=None) -> dict:
    """Delete finished workflows older than `days`. `apply=False` only counts them."""
    from dbos._workflow_commands import garbage_collect

    cutoff = int((at if at is not None else time.time()) * 1000) - days * DAY_MS
    before = counts(system_database, cutoff)
    report = {"days": days, "applied": apply, "eligible": before["eligible"], "in_flight": before["in_flight"], "stopped": False}
    if not apply or not before["eligible"]:
        report["deleted"] = 0
        return report
    owner = SimpleNamespace(_sys_db=system_database, logger=log)
    step = before["oldest"] + DAY_MS
    while True:
        garbage_collect(owner, min(step, cutoff), None, batch_size=BATCH)
        if step >= cutoff:
            break
        if stop and stop():
            report["stopped"] = True
            break
        step += DAY_MS
    report["deleted"] = before["eligible"] - counts(system_database, cutoff)["eligible"]
    return report


async def watch(service):
    """Run retention daily, off the event loop, and record the outcome where the dashboard can read it."""
    from dbos._dbos import _get_dbos_instance

    from .host_health import DRAINING

    await asyncio.sleep(FIRST_DELAY_SECONDS)
    while True:
        days = service.config.dbos_retention_days
        try:
            report = await asyncio.to_thread(prune, _get_dbos_instance()._sys_db, days, stop=DRAINING.is_set)
            await asyncio.to_thread(service.db.set_setting, SETTING, {**report, "state": "healthy", "at": now()})
        except Exception as exc:
            log.warning("DBOS retention failed", exc_info=True)
            await asyncio.to_thread(
                service.db.set_setting, SETTING, {"state": "unavailable", "at": now(), "error_type": type(exc).__name__}
            )
        await asyncio.sleep(INTERVAL_SECONDS)
