import asyncio
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from croniter import croniter
from dbos import DBOS, SetEnqueueOptions
from dbos._error import DBOSQueueDeduplicatedError
from sqlalchemy import select

from .db import Job, Run, Source, now, record
from .discovery import discover
from .admission import employer_partition
from .host_health import INTERRUPTED
from .runtime import engine_version

SERVICE = None


@DBOS.workflow(max_recovery_attempts=1)
async def apply_workflow(run_id: str):
    await SERVICE.execute(run_id)


def source_failed(db, source_id, exc, managed, timezone):
    """Record a failed scan and defer the source: a day for a managed board, longer for one that is gone or refuses us."""
    from .features.discovery import record_source
    from .features.discovery.policy import dead_source_wait, permanent_failure

    health = record_source(db, source_id, False, type(exc).__name__)
    with db.session() as s:
        row = s.get(Source, source_id)
        if row:
            row.error, row.last_run = f"Source failed: {type(exc).__name__}: {str(exc)[:180]}", now()
            if permanent_failure(exc):
                # Not-found and refused boards back off from a day up to two weeks as failures repeat.
                wait = dead_source_wait(health["failures"])
            elif managed:
                # Dead/moved career boards get a daily retry, not a request storm every two hours.
                wait = timedelta(hours=24)
            else:
                wait = None
            if wait:
                row.next_run = (datetime.now(UTC) + wait).astimezone(ZoneInfo(timezone)).isoformat()  # an instant, not wall-clock arithmetic across a DST change


@DBOS.workflow(max_recovery_attempts=1)
async def source_workflow(source_id: str):
    service = SERVICE
    from .agent_settings import enabled

    if not enabled(service.db, "discovery"):
        return
    with service.db.session() as s:
        row = s.get(Source, source_id)
        if not row or not row.enabled:
            return
        source = record(row)
    try:
        from .jobboard import is_managed, scrape_source

        managed = is_managed(service.db, source_id)
        if source["kind"] == "web_search":
            from .jobsearch import expand_sources

            ids = await expand_sources(source, service.db, service.config)
        else:
            ids = (
                await scrape_source(source, service.db, service.config)
                if managed or source["auto_queue"]
                else await discover(source, service.db, service.config)
            )
        if managed or source["auto_queue"]:
            # All autonomous applications share freshness checks, batch ranking,
            # duplicate protection and the application scheduler.
            await enqueue_matching()
        with service.db.session() as s:
            row = s.get(Source, source_id)
            row.last_run, row.error, row.found = now(), "", len(ids)
        from .features.discovery import record_source

        record_source(service.db, source_id, True)
    except Exception as exc:
        source_failed(service.db, source_id, exc, managed, service.config.timezone)


@DBOS.workflow(max_recovery_attempts=1)
async def autopilot_workflow():
    from .autopilot import tick

    await tick(SERVICE)


@DBOS.workflow(max_recovery_attempts=2)
async def tailor_workflow(job_id: str):
    from .features.resumes import model_drafter, prepare_resume
    from .features.discovery import defer
    from .execution_services import master_policy

    if master_policy(SERVICE.db):
        return

    control = SERVICE.db.get_setting("control", {})
    job_control = SERVICE.db.get_setting("job_control:" + job_id, {})
    if control.get("paused") or job_control.get("state") in {"paused", "deleted"}:
        return
    result = await prepare_resume(
        SERVICE.db, SERVICE.config, job_id, drafter=model_drafter(SERVICE.db, SERVICE.config)
    )
    if result.error:
        until = defer(SERVICE.db, job_id, result.error.code, 300 if result.error.retryable else 3600)
        from .db import Setting

        with SERVICE.db.exclusive() as session:
            request = session.get(Setting, "preparation_request:" + job_id)
            if request:
                request.value = {
                    **request.value,
                    "not_before": until,
                    "error": result.error.code,
                    "state": "pending" if result.error.retryable else "blocked",
                }
    elif result.status == "ready":
        from .execution_services import finish_preparation

        await finish_preparation(SERVICE, job_id, result.artifact.artifact_id)
    await enqueue_matching()


async def enqueue_tailoring(job_id):
    try:
        with SetEnqueueOptions(deduplication_id="tailor:" + job_id):
            await DBOS.enqueue_workflow_async("tailoring", tailor_workflow, job_id)
        return True
    except DBOSQueueDeduplicatedError:
        return False


async def enqueue_matching():
    try:
        with SetEnqueueOptions(deduplication_id="job-board-autopilot"):
            await DBOS.enqueue_workflow_async("matching", autopilot_workflow)
        return True
    except DBOSQueueDeduplicatedError:
        return False


@DBOS.workflow(max_recovery_attempts=2)
async def classification_workflow(job_ids: list[str]):
    """Ranking is background work; partial progress wakes preparation/dispatch."""
    from .features.discovery import classify, existing_rank_adapter, policy_for
    from .execution_services import prepare_due

    policy = policy_for(SERVICE.db)
    # One model call ranks up to `classify_batch` (24) postings: six at a time paid the shared profile and resumes
    # (about half of each prompt) four times over and ranked ~10 jobs a minute (2026-10-28).
    for offset in range(0, len(job_ids), policy.classify_batch):
        if SERVICE.db.get_setting("control", {}).get("paused"):
            return
        with SERVICE.db.session() as s:
            jobs = [
                {
                    k: getattr(j, k)
                    for k in ("id", "identity", "url", "title", "location", "description", "company")
                }
                for j in s.scalars(
                    select(Job).where(
                        Job.id.in_(job_ids[offset : offset + policy.classify_batch]), Job.status == "new", Job.score.is_(None)
                    )
                )
            ]
        if not jobs:
            continue
        await classify(
            SERVICE.db,
            jobs,
            existing_rank_adapter(SERVICE.db, SERVICE.config),
            policy,
            SERVICE.db.get_setting("profile", {}),
        )
        await prepare_due(SERVICE)
        await enqueue_matching()


async def enqueue_classification(job_ids):
    if not job_ids:
        return False
    try:
        with SetEnqueueOptions(deduplication_id="autonomous-classification"):
            await DBOS.enqueue_workflow_async("classification", classification_workflow, job_ids[:72])
        return True
    except DBOSQueueDeduplicatedError:
        return False


@DBOS.workflow(max_recovery_attempts=2)
async def feed_workflow():
    from .features.discovery import policy_for
    from .features.discovery.feeds import sweep

    board = SERVICE.db.get_setting("job_board_settings", {})
    if SERVICE.db.get_setting("control", {}).get("paused") or not board.get("enabled", True):
        return
    result = await sweep(SERVICE.db, policy_for(SERVICE.db).max_age_days)
    SERVICE.db.set_setting("discovery_feed_delivery", {"finished_at": now(), "state": result["state"]})
    await enqueue_matching()


async def enqueue_feed_if_due(service):
    """The public feed sweep uses the durable discovery queue, never a browser slot."""
    from datetime import timedelta
    from .features.discovery.feeds import SWEEP_KEY

    board = service.db.get_setting("job_board_settings", {})
    if service.db.get_setting("control", {}).get("paused") or not board.get("enabled", True):
        return False
    at = datetime.now(ZoneInfo(service.config.timezone))
    latest = service.db.get_setting(SWEEP_KEY, {})
    delivery = service.db.get_setting("discovery_feed_delivery", {})
    window = timedelta(hours=board.get("interval_hours", 2))
    if latest.get("state") == "error":
        window = timedelta(minutes=5)
    stamp = latest.get("finished_at") or latest.get("started_at")
    if stamp and datetime.fromisoformat(stamp) + window > at:
        return False
    if (
        delivery.get("enqueued_at")
        and datetime.fromisoformat(delivery["enqueued_at"]) + timedelta(minutes=5) > at
    ):
        return False
    try:
        with SetEnqueueOptions(deduplication_id="new-grad-feed-sweep"):
            await DBOS.enqueue_workflow_async("discovery", feed_workflow)
    except DBOSQueueDeduplicatedError:
        return False
    service.db.set_setting("discovery_feed_delivery", {"state": "queued", "enqueued_at": now()})
    return True


# A tick that runs this late was recovered after downtime (a sleeping laptop, a hang, a restart).
STALE_TICK = timedelta(minutes=5)


def stale_tick(scheduled_time, current=None):
    """Is this scheduled tick too late to act? The next live tick does its work. Hundreds of recovered ticks each took
    the global lock and a pooled connection, and exhausted the pool at every start (2026-10-20)."""
    at = scheduled_time if scheduled_time.tzinfo else scheduled_time.replace(tzinfo=UTC)
    return (current or datetime.now(UTC)) - at > STALE_TICK


@DBOS.workflow(max_recovery_attempts=1)
async def scheduled_tick(scheduled_time: datetime, context: dict):
    from .jobboard import managed_ids

    if stale_tick(scheduled_time):
        return
    SERVICE.db.set_setting("scheduler_heartbeat", {"at": now(), "engine_version": engine_version()})
    paused = SERVICE.db.get_setting("control", {}).get("paused")
    current = datetime.now(ZoneInfo(SERVICE.config.timezone))
    due = []
    managed = managed_ids(SERVICE.db)
    from .execution_services import enabled

    from .agent_settings import enabled as helper_enabled

    discovery_enabled = helper_enabled(SERVICE.db, "discovery")
    feature_sources = None
    if enabled(SERVICE.db) and discovery_enabled:
        from .features.discovery import discover_due, policy_for

        feature_sources = set((await asyncio.to_thread(discover_due, SERVICE.db, policy_for(SERVICE.db))).sources)
        await enqueue_feed_if_due(SERVICE)
    with SERVICE.db.exclusive() as s:
        for row in s.scalars(select(Source).where(Source.enabled.is_(True))):
            if not discovery_enabled:
                continue
            if paused and row.id not in managed:
                continue
            if feature_sources is not None and row.id in managed and row.id not in feature_sources:
                continue
            if datetime.fromisoformat(row.next_run) <= current:
                due.append(row.id)
                row.next_run = croniter(row.cron, current).get_next(datetime).isoformat()
    # Refill applications before processing thousands of discovery deliveries.
    # Neither a failed feed nor a deferred application controls the other queue.
    await enqueue_matching()
    try:
        from .recovery import retry_due

        await retry_due(SERVICE)
    except DBOSQueueDeduplicatedError:
        pass  # The prior matching tick is already queued/running.
    for source_id in due:
        try:
            await enqueue_source(source_id)
        except Exception:
            continue  # The source remains independently retryable on its next schedule.
    if not paused:
        from .db import Setting

        with SERVICE.db.session() as s:
            preparations = [
                row.key.removeprefix("preparation_request:")
                for row in s.scalars(select(Setting).where(Setting.key.startswith("preparation_request:", autoescape=True)))
                if row.value.get("state") == "pending" and row.value.get("not_before", "") <= now()
            ]
        for job_id in preparations[:12]:
            await enqueue_tailoring(job_id)


def recover_interrupted(service):
    # This app runs in one server process. An interrupted commit is never replayed.
    with service.db.exclusive() as s:
        for row in s.scalars(select(Run).where(Run.state.in_(["running", "submitting"]))):
            committed = row.state == "submitting"
            row.state = "submission_unknown" if committed else "waiting_browser"
            row.reason, row.finished_at = (
                "Worker interrupted during submit; reconcile before retrying" if committed else INTERRUPTED,
                now(),
            )
            s.get(Job, row.job_id).status = row.state
            if not committed:
                from .recovery import schedule_recovery

                schedule_recovery(s, service.config, row, {"recovery_cause": "worker_interrupted"})


async def start(service):
    global SERVICE
    SERVICE = service
    recover_interrupted(service)
    from .recovery import schedule_version_repairs

    schedule_version_repairs(service)
    from . import dbos_sqlite  # noqa: F401 - old SQLite needs the pinned DBOS clock default

    DBOS(
        config={
            "name": "applypilot-studio",
            "system_database_url": service.config.dbos_database_url,
            "application_version": engine_version(),
            # DBOS's executor is unbounded by default: after a night of source sweeps the process held 2,942 idle
            # executor threads (2026-10-22), and a sampling profiler could not keep up with them.
            "max_executor_threads": 128,
        }
    )
    DBOS.launch()
    await retire_obsolete_deliveries(service)
    from .pipeline_comparison import POOLS, pool_slot_ids

    for pool in POOLS:
        count = len(pool_slot_ids(pool, service.config.topology))
        if not count:
            continue
        await DBOS.register_queue_async(
            "applications_" + pool,
            worker_concurrency=count,
            global_concurrency=count,
            partition_concurrency=1,
        )
    await DBOS.register_queue_async(
        "discovery",
        worker_concurrency=service.config.discovery_workers,
        global_concurrency=service.config.discovery_workers,
    )
    await DBOS.register_queue_async("matching", worker_concurrency=1, global_concurrency=1)
    await DBOS.register_queue_async("classification", worker_concurrency=1, global_concurrency=1)
    await DBOS.register_queue_async("tailoring", worker_concurrency=2, global_concurrency=2)

    async def dispatch(run_id):
        with service.db.session() as s:
            run = s.get(Run, run_id)
            if not run or run.state != "queued":
                return
            job = s.get(Job, run.job_id)
            host = employer_partition(job)
            from .pipeline_comparison import pool_for

            queue = "applications_" + pool_for(run)
        # Partitioned DBOS queues do not support dedup IDs. The application row's
        # atomic queued -> running transition provides delivery idempotency.
        urgent = bool(run.packet.get("email_wait_resume") or run.packet.get("verification_only"))
        # DBOS priorities start at 1. A zero priority crashes urgent delivery,
        # including startup when a verification continuation is already queued.
        with SetEnqueueOptions(queue_partition_key=host, priority=1 if urgent else 10):
            await DBOS.enqueue_workflow_async(queue, apply_workflow, run_id)

    service.dispatch = dispatch
    if not await DBOS.get_schedule_async("sources"):
        await DBOS.create_schedule_async(
            schedule_name="sources",
            workflow_fn=scheduled_tick,
            schedule="* * * * *",
            context={},
            cron_timezone=service.config.timezone,
        )
    await ensure_scheduler(service)
    # Business outbox recovery: enqueue anything committed before a process crash.
    with service.db.session() as s:
        pending = list(s.scalars(select(Run.id).where(Run.state == "queued")))
    for run_id in pending:
        await dispatch(run_id)
    try:
        # Open questions the owner has meanwhile answered for another job, worded the same (no model call).
        await service.settle_open_questions()
    except Exception:
        pass  # They stay asked; nothing else depends on this.
    try:
        # Open questions this build closes, answers or recognizes as its own misreads (no model call).
        await service.revalidate_open_questions()
    except Exception:
        pass  # They stay asked.


async def retire_obsolete_deliveries(service):
    """Release old-version queue leases; business rows own restart and submit safety.

    This server is the sole executor for its DBOS database. Versioned deliveries
    cannot execute on the new build, but still hold deduplication/concurrency.
    Keep their history, and never fork/replay a past submission workflow.
    """
    version = engine_version()
    obsolete, source_ids = [], set()
    offset = 0
    while True:
        rows = await DBOS.list_workflows_async(
            status=["ENQUEUED", "PENDING"],
            queue_name=[
                "matching",
                "discovery",
                "applications",
                "applications_oct04",
                "applications_experimental",
                "applications_current",
                "applications_normal",
                "applications_specialist",
                "tailoring",
                "classification",
            ],
            limit=1000,
            offset=offset,
            load_input=True,
            load_output=False,
        )
        for row in rows:
            if row.app_version == version:
                continue
            args = (row.input or {}).get("args", ())
            if row.name == "apply_workflow":
                if not args:
                    continue
                with service.db.session() as s:
                    run = s.get(Run, args[0])
                    if run and run.state in {"running", "submitting"}:
                        continue  # An external commit must be reconciled, not interrupted.
            elif row.name == "source_workflow":
                if args:
                    source_ids.add(args[0])
            elif row.name not in {
                "autopilot_workflow",
                "tailor_workflow",
                "feed_workflow",
                "classification_workflow",
            }:
                continue
            obsolete.append(row.workflow_id)
        if len(rows) < 1000:
            break
        offset += len(rows)
    for start_at in range(0, len(obsolete), 1000):
        await DBOS.cancel_workflows_async(obsolete[start_at : start_at + 1000])
    if source_ids:
        with service.db.session() as s:
            for source in s.scalars(
                select(Source).where(Source.id.in_(source_ids), Source.enabled.is_(True))
            ):
                source.next_run = now()
    service.db.set_setting(
        "queue_version_recovery",
        {"at": now(), "engine_version": version, "retired": len(obsolete), "sources_due": len(source_ids)},
    )


async def enqueue_source(source_id):
    try:
        with SetEnqueueOptions(deduplication_id=source_id):
            await DBOS.enqueue_workflow_async("discovery", source_workflow, source_id)
        return True
    except DBOSQueueDeduplicatedError:
        return False  # Already in flight; never interrupt the application scheduler.


async def resume(service):
    await ensure_scheduler(service)
    with service.db.session() as s:
        ids = list(s.scalars(select(Run.id).where(Run.state == "queued")))
    for run_id in ids:
        # A paused workflow may have completed without starting; use a new delivery ID,
        # while the application state's CAS prevents a second active browser.
        await service.dispatch(run_id)


def stop():
    DBOS.destroy()


async def ensure_scheduler(service, *, force=False):
    """Explicit unattended start restores maintenance suspension, never a user pause."""
    schedule = await DBOS.get_schedule_async("sources")
    if schedule is None:
        return {"state": "missing", "degraded": True}
    control = service.db.get_setting("control", {})
    board = service.db.get_setting("job_board_settings", {})
    hold = service.db.get_setting("execution_v2_deployment_hold", {})
    enabled = not control.get("paused") and (force or board.get("auto_apply"))
    if (
        enabled
        and (force or hold.get("stage") not in {"draining", "canary"})
        and schedule["status"] == "PAUSED"
    ):
        await asyncio.to_thread(DBOS.resume_schedule, "sources")
        schedule = await DBOS.get_schedule_async("sources")
    status = {
        "state": schedule["status"].lower(),
        "last_fired_at": schedule.get("last_fired_at"),
        "checked_at": now(),
        "degraded": enabled and schedule["status"] != "ACTIVE",
    }
    service.db.set_setting("scheduler_health", status)
    return status


async def monitor_scheduler(service):
    while True:
        try:
            status = await ensure_scheduler(service)
            if status.get("state") == "active":
                heartbeat = service.db.get_setting("scheduler_heartbeat", {})
                if heartbeat.get("at"):
                    age = (
                        datetime.now(ZoneInfo(service.config.timezone))
                        - datetime.fromisoformat(heartbeat["at"])
                    ).total_seconds()
                    status["degraded"] = age > 180
                    status["heartbeat_age_seconds"] = round(age)
                    service.db.set_setting("scheduler_health", status)
            # Keep matching responsive between cron ticks. DBOS deduplication
            # guarantees one durable ranking owner; this is only a wake signal.
            board = service.db.get_setting("job_board_settings", {})
            control = service.db.get_setting("control", {})
            hold = service.db.get_setting("execution_v2_deployment_hold", {})
            if (
                board.get("auto_apply")
                and not control.get("paused")
                and hold.get("stage") not in {"draining", "canary"}
            ):
                await enqueue_matching()
        except Exception:
            service.db.set_setting(
                "scheduler_health", {"state": "unavailable", "degraded": True, "checked_at": now()}
            )
        await asyncio.sleep(service.config.autopilot_refill_seconds)
