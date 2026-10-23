"""Executing a queued run: pool semaphores, slot claim, the engine under a deadline, the submission reservation and shutdown drain."""

import asyncio
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select

from ..db import Budget, Job, Run, Setting, now, record
from ..admission import QueueBlocked, employer_partition


class ExecutionMixin:
    def reserve_submission(self, run_id):
        day = datetime.now(ZoneInfo(self.config.timezone)).date().isoformat()
        with self.db.exclusive() as s:
            run = s.get(Run, run_id)
            job = s.get(Job, run.job_id)
            from ..job_control import require_running

            require_running(s, run.job_id)
            from ..application_budget import require_allowance

            require_allowance(s, run.job_id, new_attempt=False)
            control = s.get(Setting, "control").value
            if control.get("paused"):
                raise ValueError("Workers were paused before submission")
            from ..host_health import ServerDraining, draining

            if draining():
                # A stop (deploy) never cuts a commit: none may begin now, and Service.drain waits for those under way.
                # Checked inside this transaction, which `committing` shares, so no commit can slip in unseen.
                raise ServerDraining()
            if run.state != "running" or run.mode != "submit":
                raise ValueError("Submission is not authorized for this run")
            if not job.demo:
                if not control.get("auto_submit"):
                    raise ValueError("Automatic submission was disabled")
                from ..application_pacing import require_company_limit, require_ats_focus
                from ..owner_apply import picked

                if not picked(s, job.id):  # The owner's own pick passed these at the queue on purpose.
                    require_ats_focus(s, job, run)
                    require_company_limit(s, job, run_id)
                budget = s.get(Budget, day)
                if not budget:
                    budget = Budget(day=day, spent=0, submissions=0)
                    s.add(budget)
                if budget.submissions >= self.config.daily_application_limit:
                    raise ValueError("Daily submission cap reached")
                budget.submissions += 1
            run.state = "submitting"

    def committing(self):
        """Applications whose commit is in flight (or whose verification continuation is). Holds the reservation lock."""
        with self.db.exclusive() as s:
            return list(s.scalars(select(Run.id).where(Run.state == "submitting")))

    async def drain(self, grace):
        """Make a stop safe: start nothing, begin no commit, and wait up to `grace` seconds for commits under way.

        Called by the app's shutdown (uvicorn's SIGTERM from `docker compose up/stop/restart`), so every deploy goes
        through it. A commit that finishes is recorded as usual; one that outlives `grace` is left `submitting` and
        becomes submission_unknown at the next start (workers.recover_interrupted). Returns the run ids still
        committing when it gave up, also saved as the `last_drain` setting.
        """
        import time

        from .. import host_health

        host_health.begin_drain()
        began = time.monotonic()
        left = await asyncio.to_thread(self.committing)
        while left and time.monotonic() - began < grace:
            await asyncio.sleep(host_health.DRAIN_POLL_SECONDS)
            left = await asyncio.to_thread(self.committing)
        await asyncio.to_thread(
            self.db.set_setting,
            "last_drain",
            {"at": now(), "waited_seconds": round(time.monotonic() - began), "still_committing": left},
        )
        return left

    def kick_outbox(self):
        if getattr(self, "outbox_running", False):
            return
        from ..outbox import process_one

        task = asyncio.create_task(process_one(self))
        self.notification_tasks.add(task)
        task.add_done_callback(self.notification_tasks.discard)

    async def execute(self, run_id):
        import asyncio

        from ..pipeline_comparison import POOLS, pool_for, pool_slot_ids

        if not hasattr(self, "browser_pools"):
            self.browser_pools = {
                pool: asyncio.Semaphore(len(pool_slot_ids(pool, self.config.topology))) for pool in POOLS
            }
        with self.db.session() as s:
            run = s.get(Run, run_id)
            if not run or run.state != "queued":
                return
            pool = pool_for(run)
        async with self.browser_pools[pool]:
            await self._execute_owned(run_id)

    async def _execute_owned(self, run_id):
        import time

        from ..browser import BrowserUnavailable
        from ..pipeline_comparison import engine_for, slots

        # The database claim also caps separate CLI processes and duplicate deliveries.
        # Never hold the transaction while waiting for a free browser slot.
        budget_checked = False
        first_attempt = False
        while True:
            claimed = False
            idle = 1
            with self.db.exclusive() as s:
                row = s.get(Run, run_id)
                if not row or row.state != "queued":
                    return
                if s.get(Job, row.job_id).status == "archived":
                    row.state, row.finished_at = "cancelled", now()
                    return
                from ..job_control import state as job_state

                if job_state(self.db, row.job_id).get("state") == "paused":
                    row.state, row.finished_at = "paused", now()
                    return
                if s.get(Setting, "control").value.get("paused"):
                    return
                from .. import host_health

                if host_health.draining():
                    return  # The server is stopping: the run stays queued and starts after the restart.
                # A run queued earlier still waits while the host runs late or the worker Chrome is gone.
                gate = host_health.check(self.config.workers, session=s)
                from ..application_budget import require_allowance

                try:
                    if not budget_checked:
                        from ..application_budget import max_seconds

                        cap = max_seconds(s)
                        deadline, remaining = require_allowance(s, row.job_id, exclude_run=row.id)
                        first_attempt = remaining is not None and remaining == cap
                        budget_checked = True
                    elif first_attempt:
                        # Initial queue time is free. Avoid re-reading all alias
                        # history every second while waiting behind an employer.
                        deadline = datetime.now(UTC) + timedelta(seconds=cap)
                    elif deadline is not None and deadline <= datetime.now(UTC):
                        raise QueueBlocked(
                            "Application reached its eight-minute total deadline", code="application_deadline"
                        )
                except QueueBlocked as exc:
                    row.state, row.finished_at, row.reason = (
                        "submission_unknown" if row.packet.get("verification_only") else "timed_out",
                        now(),
                        str(exc),
                    )
                    current_job = s.get(Job, row.job_id)
                    current_job.status = (
                        "submission_unknown" if row.packet.get("verification_only") else "timed_out"
                    )
                    recovery = s.get(Setting, "draft_recovery:" + row.job_id)
                    if recovery:
                        recovery.value = {**recovery.value, "scheduled": False, "status": exc.code}
                    return
                occupied = len(
                    list(s.scalars(select(Run.id).where(Run.state.in_(["running", "submitting"]))))
                )
                partition = employer_partition(s.get(Job, row.job_id))
                same_employer = any(
                    employer_partition(j) == partition
                    for j in s.scalars(
                        select(Job)
                        .join(Run, Run.job_id == Job.id)
                        .where(Run.state.in_(["running", "submitting"]))
                    )
                )
                from ..application_pacing import may_start, record_start

                current_job = s.get(Job, row.job_id)
                recovery_row = s.get(Setting, "draft_recovery:" + row.job_id)
                # An emailed code expires: a continuation is not held back for load, only for a dead browser or a stop.
                urgent = bool(row.packet.get("email_wait_resume") or row.packet.get("verification_only"))
                allowed = (
                    self.config.workers
                    if urgent and gate.code in {"host_loaded", "host_overloaded"}
                    else min(self.config.workers, gate.limit)
                )
                if (
                    occupied < allowed
                    and not same_employer
                    and slots(s, row, self.config.topology)
                    and may_start(s, current_job, row)
                    and self.mail.recovery_sender_ready(
                        s,
                        current_job,
                        recovery_row.value if recovery_row else None,
                    )
                    and self.mail.shared_sender_free(s, current_job)
                ):
                    from ..application_slots import claim_slot

                    record_start(s, current_job, row)
                    claim_slot(s, row, self.config.topology)
                    row.state, row.started_at = (
                        ("submitting" if row.packet.get("verification_only") else "running"),
                        now(),
                    )
                    # Uncapped production (owner 2026-10-18) carries no lifetime deadline: the run's own
                    # runner budget below ends it, and mail/owner waits may extend it as before.
                    packet = {k: v for k, v in row.packet.items() if k != "application_deadline"}
                    row.packet = packet | ({"application_deadline": deadline.isoformat()} if deadline else {})
                    s.get(Job, row.job_id).status = "running"
                    run, job = record(row), record(s.get(Job, row.job_id))
                    claimed = True
                else:
                    idle = 5 if gate.limit == 0 else 1
            if claimed:
                break
            await asyncio.sleep(idle)
        started = time.monotonic()
        try:
            with self.db.session() as s:
                self.check_cooldown(s, s.get(Job, run["job_id"]))
            from ..specialist import pool_boost
            from ..tiers import clamp, time_budget

            # The outer deadline follows the run's own (tier, ATS and worker pool) budget plus cleanup;
            # a fixed base cut tier-2/3 runs off at ~945 s.
            outer = time_budget(
                max(self.config.application_timeout, self.config.multipage_timeout),
                clamp((run.get("packet") or {}).get("runner_tier", 1)),
                job.get("ats", ""),
                boost=pool_boost(self.config, (run.get("packet") or {}).get("worker_pool")),
            )
            run_budget = max(outer, self.config.application_timeout, self.config.multipage_timeout) + 45
            lifetime = (run.get("packet") or {}).get("application_deadline")
            async with asyncio.timeout(
                min(
                    run_budget, max(0, (datetime.fromisoformat(lifetime) - datetime.now(UTC)).total_seconds())
                )
                if lifetime
                else run_budget
            ):
                from ..job_control import watch

                engine = engine_for(self, run)
                execution = asyncio.create_task(engine.run(job, run))
                monitor = asyncio.create_task(watch(self, engine, execution, run_id))
                try:
                    result = await execution
                except asyncio.CancelledError:
                    if engine.result and engine.result.get("state") in {"paused", "cancelled"}:
                        result = engine.result
                    else:
                        raise
                finally:
                    monitor.cancel()
                    await asyncio.gather(monitor, return_exceptions=True)
        except TimeoutError:
            with self.db.session() as s:
                current = s.get(Run, run_id)
                committed = current and current.state == "submitting"
            result = {
                "state": "submission_unknown" if committed else "timed_out",
                "reason": "Application reached its eight-minute total deadline; saved draft and submission protection retained",
                "recovery_cause": "operation_timeout" if not committed else "",
            }
        except BrowserUnavailable as exc:
            # Infrastructure, not the application: wait for the browser and retry
            # without spending a repair attempt.
            result = {
                "state": "timed_out",
                "reason": str(exc)[:400],
                "blocker_type": "site_transient",
                "recovery_cause": "browser_unavailable",
            }
        except Exception as exc:
            from ..agent_settings import AgentDisabled

            # Keep provider payloads and secrets out of the event stream.
            known = isinstance(exc, (ValueError, RuntimeError)) and "api_key" not in str(exc).lower()
            result = {
                "state": "waiting_browser" if isinstance(exc, AgentDisabled) else "failed",
                "reason": str(exc)[:400] if known else f"Worker error: {type(exc).__name__}",
            }
            if isinstance(exc, AgentDisabled):
                result.update(blocker_type="agent_disabled", recovery_cause="agent_disabled", retryable=False)
            import traceback
            from pathlib import Path

            frames = [f for f in traceback.extract_tb(exc.__traceback__) if "/jobpilot/" in f.filename]
            result["diagnostics"] = {
                "exception": type(exc).__name__,
                "locations": [f"{Path(f.filename).name}:{f.lineno}:{f.name}" for f in frames[-5:]],
            }
            self._trace_failure(run_id, result["diagnostics"])
        self.complete(run_id, result, time.monotonic() - started)

        self.kick_outbox()
