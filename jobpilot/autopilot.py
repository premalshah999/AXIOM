"""A durable unattended batch. Only verified fresh US jobs enter the normal workers."""

import asyncio
import hashlib
import json
import re
import time
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo
from sqlalchemy import func, select, update

from .db import Budget, Job, Run, Source, Setting, latest_runs, now
from .discovery import rank_batch
from .jobboard import FITS, FOREIGN, LOCKED, board_snapshot, fit_of, settings
from .service import ACTIVE
from .admission import active_runs, capacity, employer_partition, outcome_for


PRODUCTION_TARGET = "production:100_new_confirmed_20261012"


# A confirmed run's resume hash never changes, and reading it parses the run's whole ~320 KB json packet in PostgreSQL.
# /api/runtime-health read it for every confirmed run on each poll: 2,536 packets, 4-6 s of database CPU a call, from
# the Batch and Setup pages every 30 s (2026-10-29). Read once per run, then remembered.
_RESUME_SHA = {}


def _resume_shas(session, run_ids):
    missing = [rid for rid in run_ids if rid not in _RESUME_SHA]
    for offset in range(0, len(missing), 200):
        chunk = missing[offset : offset + 200]
        for rid, sha in session.execute(
            select(Run.id, Run.packet["resume_sha"].as_string()).where(Run.id.in_(chunk))
        ):
            if sha:  # an absent hash is asked again next time
                _RESUME_SHA[rid] = sha
    return {rid: _RESUME_SHA.get(rid) for rid in run_ids}


def production_progress(db):
    """Count new, evidenced submissions since the applicant's explicit baseline."""
    goal = db.get_setting(PRODUCTION_TARGET, {})
    if not goal:
        return None
    excluded = set(goal.get("excluded_job_ids", []))
    previous = set(goal.get("baseline_run_ids", []))
    confirmed = set()
    with db.session() as session:
        # Light columns only: full Run rows carry ~320 KB packets each, and loading every confirmed
        # run per /api/runtime-health call blocked the loop and bloated the process (2026-10-15).
        runs = session.execute(
            select(Run.id, Run.job_id, Run.receipt)
            .join(Job, Job.id == Run.job_id)
            .where(
                Run.state == "confirmed",
                Job.demo.is_(False),
                Run.created_at >= goal["started_at"],
            )
        ).all()
        website_proof = []
        for run in runs:
            receipt = run.receipt or {}
            if (
                run.id in previous
                or run.job_id in excluded
                or receipt.get("evidence_reconciliation") == "pending"
                or any(
                    receipt.get(key)
                    for key in (
                        "reconciled_existing_submission",
                        "existing_application_notice",
                        "matched_existing_application",
                    )
                )
            ):
                continue
            website = (
                receipt.get("type") == "explicit_confirmation_page"
                and receipt.get("evidence_version") == 2
                and receipt.get("website_confirmed")
                and receipt.get("job_id") == run.job_id
                and receipt.get("run_id") == run.id
                and receipt.get("evidence_selector")
                and receipt.get("status_region")
                and receipt.get("confirmation")
                and receipt.get("resume_sha256")
            )
            email = receipt.get("email") or {}
            email_proof = (
                isinstance(email, dict)
                and email.get("authenticated")
                and email.get("sender_trusted")
                and email.get("message_id")
                and email.get("job_id") == run.job_id
                and not email.get("matched_existing_application")
            )
            if email_proof:
                confirmed.add(run.job_id)
            elif website:
                website_proof.append((run, receipt))
        # The packet is read only for a run whose receipt claims a website confirmation and nothing else proves it.
        shas = _resume_shas(session, [run.id for run, _ in website_proof])
        for run, receipt in website_proof:
            if shas[run.id] and receipt.get("resume_sha256") == shas[run.id]:
                confirmed.add(run.job_id)
    return {
        "started_at": goal["started_at"],
        "target": goal["target"],
        "confirmed": len(confirmed),
        "remaining": max(0, goal["target"] - len(confirmed)),
        "state": "complete" if len(confirmed) >= goal["target"] else "active",
    }


async def start_autonomous(service, target=100, refresh_sources=True):
    """The same durable unattended launch for web, Telegram and startup."""
    from .jobboard import setup_sources, managed_ids
    from .workers import ensure_scheduler, enqueue_matching

    if not service.dispatch:
        raise ValueError("Application workers are disabled")
    if not 1 <= target <= 500:
        raise ValueError("Choose a batch target from 1 to 500")
    db = service.db
    control = db.get_setting("control", {})
    db.set_setting("control", {**control, "paused": False, "auto_submit": True})
    # A new unattended batch uses the integrated readiness/tailoring contract.
    db.set_setting("execution_services", {"enabled": True, "version": 1})
    cfg = settings(db).model_copy(update={"enabled": True, "auto_apply": True, "batch_target": target})
    setup_sources(db, cfg)
    batch = start_batch(db, target)
    # A previously rejected discovery can become ready after ranking/criteria
    # changes. Resume reconsiders only approved jobs with no application history.
    with db.session() as s:
        newly_eligible = set(
            s.scalars(
                select(Job.id).where(
                    Job.status == "ready",
                    Job.demo.is_(False),
                    Job.score >= cfg.minimum_auto_fit,
                    Job.id.not_in(select(Run.job_id)),
                )
            )
        )
    reconsidered = newly_eligible & set(batch.get("considered_ids", []))
    if reconsidered:
        batch = {
            **batch,
            "considered_ids": [jid for jid in batch["considered_ids"] if jid not in reconsidered],
        }
        persist_batch(db, batch)
    count = 0
    if refresh_sources:
        requested = now()
        with db.session() as s:
            count = s.execute(
                update(Source)
                .where(Source.id.in_(managed_ids(db)), Source.enabled.is_(True), Source.error == "")
                .values(next_run=requested)
            ).rowcount
        db.set_setting("job_board_requested", {"at": requested})
    scheduler = (
        await ensure_scheduler(service, force=True)
        if service.config.enable_workers
        else {"state": "disabled"}
    )
    await enqueue_matching()
    return {
        "batch": batch,
        "discovery": {"scheduled_sources": count, "reconsidered_jobs": len(reconsidered)},
        "scheduler": scheduler,
    }


def confirmed_since(session, started_at):
    """Distinct non-demo jobs with a confirmed, evidence-settled run created at or after `started_at`, any origin."""
    if not started_at:
        return set()
    return set(
        session.scalars(
            select(Run.job_id)
            .distinct()
            .join(Job, Job.id == Run.job_id)
            .where(
                Run.state == "confirmed",
                Job.demo.is_(False),
                Run.created_at >= started_at,
                Run.receipt["evidence_reconciliation"].as_string().is_distinct_from("pending"),
            )
        )
    )


def batch_snapshot(service):
    """Authoritative distinct-job progress, never a count of retry attempts."""
    from .db import JobListing
    from .jobboard import us_location
    from .pipeline_comparison import snapshot as worker_snapshot

    db, cfg = service.db, settings(service.db)
    batch = db.get_setting("job_board_autopilot", {})
    control = db.get_setting("control", {})
    hold = db.get_setting("execution_v2_deployment_hold", {})
    at = datetime.now(UTC)
    with db.session() as s:
        # Each batch job's newest run and its confirmation, in SQL: every run of the installation was read and
        # sorted here on each 6-second dashboard poll (2026-10-22).
        owned_ids = list(dict.fromkeys(batch.get("run_ids", [])))
        latest, confirmed = {}, set()
        if owned_ids:
            owned_jobs = select(Run.job_id).where(Run.id.in_(owned_ids))
            newest = latest_runs(Run.job_id.in_(owned_jobs))
            latest = dict(s.execute(select(newest.c.job_id, newest.c.state)).all())
            # Recovery owns a new run ID for the same job; its verified receipt
            # belongs to this batch even when the original attempt failed. The same count as the tick's.
            confirmed = set(
                s.scalars(
                    select(Run.job_id)
                    .distinct()
                    .where(
                        Run.job_id.in_(owned_jobs),
                        Run.state == "confirmed",
                        Run.receipt["evidence_reconciliation"].as_string().is_distinct_from("pending"),
                    )
                )
            )
        # Goal progress is every distinct job confirmed since the batch started, whoever started it: the owner's
        # Apply, bulk Apply and apply-many runs are never in run_ids, so the batch showed 220 while 300+ were
        # confirmed and the owner could not tell whether the goal was met (2026-10-29).
        confirmed_all = confirmed | confirmed_since(s, batch.get("started_at"))
        batch_jobs = set(latest)
        controls = {
            row.key.removeprefix("job_control:"): row.value.get("state")
            for row in s.scalars(select(Setting).where(Setting.key.like("job_control:%")))
        }
        requested = {
            key.removeprefix("owner_apply:")
            for key in s.scalars(select(Setting.key).where(Setting.key.like("owner_apply:%")))
        }
        # Columns only: whole Job rows carried every fresh posting's description into each poll.
        fresh = list(
            s.execute(
                select(
                    Job.id,
                    Job.status,
                    Job.score,
                    JobListing.location,
                    JobListing.country,
                    JobListing.career_level,
                    Job.id.in_(select(Run.job_id)).label("attempted"),
                )
                .select_from(JobListing)
                .join(Job, Job.id == JobListing.job_id)
                .where(
                    JobListing.active.is_(True),
                    JobListing.country == "US",
                    Job.demo.is_(False),
                    Job.status != "archived",
                    JobListing.posted_at >= (at - timedelta(hours=cfg.max_age_hours)).isoformat(),
                    JobListing.posted_at <= at.isoformat(),
                    JobListing.last_seen
                    >= (at - timedelta(hours=max(6, cfg.interval_hours * 2 + 1))).isoformat(),
                )
            )
        )
        fresh = [
            j
            for j in fresh
            if us_location(j.location, j.country)
            and (not cfg.early_career_only or j.career_level != "Experience unspecified")
        ]
        considered = set(batch.get("considered_ids", []))
        deferred = batch.get("deferred", {})
        from .reserved import held

        # Reserved employers wait for the owner's approval in Jobs › Reserved (2026-10-27): neither eligible nor
        # Discovered (routes/job_board.unapplied leaves them out too).
        reserved = set(held(s))
        eligible = [
            j
            for j in fresh
            if not j.attempted
            and j.id not in reserved
            and j.id not in considered
            and controls.get(j.id) not in {"paused", "deleted"}
            and deferred.get(j.id, {}).get("not_before", "") <= at.isoformat()
            and (j.score is None or (j.status == "ready" and j.score >= cfg.minimum_auto_fit))
        ]
        # Active runs once for both the capacity and the worker list (each read parses their packets).
        active_rows = active_runs(s)
        capacity_now = capacity(s, service.config.workers, rows=active_rows)
        # Jobs › Discovered: fresh listings not applied to yet (routes/job_board.unapplied), the same jobs.
        discovered = [
            j
            for j in fresh
            if not j.attempted
            and j.id not in reserved
            and j.id not in requested
            and j.status not in LOCKED
            and controls.get(j.id) != "paused"
        ]
        due_sources = s.scalar(
            select(func.count()).select_from(Source).where(Source.enabled.is_(True), Source.next_run <= now())
        )
    state = batch.get("state", "idle")
    intake = bool(
        state == "active"
        and cfg.enabled
        and cfg.auto_apply
        and control.get("auto_submit")
        and not control.get("paused")
        and hold.get("stage") not in {"draining", "canary"}
    )
    blocked = sum(latest[j] in {"waiting_browser", "submission_unknown", "timed_out"} for j in batch_jobs)
    awaiting = sum(latest[j] in {"waiting_answer", "needs_review"} for j in batch_jobs)
    # Parked on an emailed code or link; the matched email resumes it. Neither blocked nor failed.
    waiting_email = sum(latest[j] == "waiting_email" for j in batch_jobs)
    failed = sum(latest[j] in {"failed", "closed", "skipped", "cancelled"} for j in batch_jobs)
    reason = "Complete" if state == "complete" else "Not started"
    action = "Start a batch to discover, rank and apply automatically."
    if state == "active":
        if control.get("paused") or not cfg.auto_apply:
            reason, action = "Paused", "Resume the batch once; applications then refill automatically."
        elif hold.get("stage") in {"draining", "canary"}:
            reason, action = (
                "Deployment hold",
                "Existing application evidence is preserved while intake is held.",
            )
        elif batch.get("waiting_for_daily_limit"):
            reason, action = "Daily limit", "The batch resumes automatically when the daily limit resets."
        elif capacity_now.occupied:
            reason, action = "Applying", "Independent workers apply now and refill as each slot becomes free."
        elif eligible:
            reason, action = (
                "Ranking and dispatching",
                "Fresh matching jobs are ranked in batches and queued automatically.",
            )
        else:
            reason, action = (
                "Discovering fresh jobs",
                "Current inventory is exhausted; discovery continues automatically. No manual queueing is needed.",
            )
    if state == "complete":
        action = "The confirmed-submission target was reached. Start another batch when ready."
    return {
        "state": state,
        "target": batch.get("target", cfg.batch_target),
        "confirmed": len(confirmed_all),
        # The batch's own share; the rest were started by the owner (Apply, bulk Apply, apply-many).
        "confirmed_by_batch": len(confirmed),
        "confirmed_by_owner": len(confirmed_all - confirmed),
        "queued": sum(latest[j] == "queued" for j in batch_jobs),
        "dispatched": batch.get("queued", 0),
        "attempted_jobs": len(batch_jobs),
        "active": capacity_now.occupied,
        "application_workers": worker_snapshot(db, service.config.topology, rows=active_rows),
        "awaiting_answer": awaiting,
        "waiting_email": waiting_email,
        "blocked": blocked,
        "failed": failed,
        "remaining": max(0, batch.get("target", cfg.batch_target) - len(confirmed_all)),
        "intake_enabled": intake,
        "intake_reason": reason,
        "next_action": action,
        "last_tick": batch.get("last_tick"),
        "scheduler": db.get_setting("scheduler_health", {}),
        "inventory": {
            "fresh": len(fresh),
            # Jobs › Discovered: fresh listings not applied to yet (routes/job_board.unapplied), the same count.
            "discovered": len(discovered),
            # Its fit filters, so "N eligible" here opens a Discovered list of N.
            "fits": {fit: sum(fit_of(j, cfg.minimum_auto_fit) == fit for j in discovered) for fit in FITS},
            "unattempted": len(eligible),
            "ready": sum(j.status == "ready" for j in eligible),
            "unranked": sum(j.score is None for j in eligible),
            "sources_due": due_sources,
        },
    }


def replenish_discovery(service, unattempted):
    """Wake productive/search sources when idle; retain source-level cooldowns."""
    from .jobboard import managed_ids

    db = service.db
    previous = db.get_setting("autopilot_replenish", {})
    at = datetime.now(UTC)
    if unattempted >= service.config.workers * 2 or (
        previous.get("at") and at - datetime.fromisoformat(previous["at"]) < timedelta(minutes=15)
    ):
        return 0
    ids = managed_ids(db)
    with db.session() as s:
        rows = list(s.scalars(select(Source).where(Source.id.in_(ids), Source.enabled.is_(True))))
        # Productive feeds and broad discovery get the next request before dead
        # catalogs. A feed with an error keeps its normal retry time.
        rows = [r for r in rows if not r.error and (r.kind == "web_search" or r.found > 0)]
        rows.sort(key=lambda r: (r.kind != "web_search", r.last_run or ""))
        for r in rows[:60]:
            r.next_run = now()
    db.set_setting("autopilot_replenish", {"at": now(), "sources_due": min(60, len(rows))})
    return min(60, len(rows))


# Why a candidate waited in a tick rather than being refused (autopilot.screen), shown beside the refusals.
WAITING = {
    "focus": "The job's platform is set aside by the saved platform focus",
    "busy": "Another application at this employer is running or queued; this one starts when it ends",
    "tailoring": "Preparing a tailored resume; it starts when ready",
}
REJECTIONS_KEPT = 400


def remember_rejection(batch, job_id, reason, code="", outcome="", at=None):
    """The latest reason each job was not started, bounded to the newest REJECTIONS_KEPT jobs.

    `skipped` kept only the last 100 refusals and a waiting job left none, so 143 Ready jobs the owner's own Apply
    then started had no recorded reason (2026-10-29)."""
    kept = batch.setdefault("rejections", {})
    kept.pop(job_id, None)  # Re-insert last: the oldest reasons go first.
    kept[job_id] = {"reason": str(reason)[:240], "code": code, "outcome": outcome, "at": at or now()}
    while len(kept) > REJECTIONS_KEPT:
        kept.pop(next(iter(kept)))


# How often an autopilot rejection of a Ready job is reconsidered (see the tick's admission_state).
RECONSIDER_SECONDS = 1800


def start_batch(db, target=100):
    previous = db.get_setting("job_board_autopilot", {})
    if previous.get("state") == "active":
        if target > previous.get("target", 0):
            # A raised goal applies to the running batch (owner 2026-10-29: 150 more past 200); saving settings with a
            # lower or default target never shrinks it.
            previous["target"] = target
            db.set_setting("job_board_autopilot", previous)
        return previous  # Refreshing settings never restarts a batch or duplicates jobs.
    batch = {
        "state": "active",
        "target": target,
        "started_at": now(),
        "run_ids": [],
        "considered_ids": [],
        "confirmed": 0,
        "queued": 0,
        "skipped": [],
        "last_tick": None,
    }
    db.set_setting("job_board_autopilot", batch)
    return batch


def persist_batch(db, batch):
    current = db.get_setting("job_board_autopilot", {})
    if current.get("started_at") == batch["started_at"]:
        # queue() committed these IDs before dispatch; never overwrite its durable outbox on an error.
        batch["run_ids"] = list(dict.fromkeys([*current.get("run_ids", []), *batch["run_ids"]]))
        batch["queued"] = len(batch["run_ids"])
    db.set_setting("job_board_autopilot", batch)


# All authorized hard ATSs use the same bounded recovery admission.
PERSIST_ATS = {
    "workday", "successfactors", "oracle", "dayforce", "adp", "eightfold", "taleo", "smartrecruiters", "icims",
    # Greenhouse and Ashby stalls (an 8-minute cap, a busy worker) were never retried (5 Greenhouse jobs, 2026-10-18).
    "greenhouse", "ashby",
}
PERSIST_SKIP = re.compile(
    # Owner 2026-10-27: refused passwords and missing verification emails are retried (resets are always allowed).
    r"captcha|security check|\bsms\b|employer already has|closed|no longer available|identity conflict|"
    r"person not found|archiv|social security|already applied|clearance|polygraph|model budget|"
    # By Light (iCIMS 2026-10-27) was retried twice after the employer locked the account: each try extends a lock.
    r"account has been (?:blocked|locked)|locked the account|account is locked|two-step verification",
    re.I,
)
# Local repairs wait 30/60 seconds, within the shared eight-minute lifetime.
# Maintenance retains its outage delay; three lifetime starts prevent storms.
PERSIST_DAILY = 3  # Initial attempt plus two retries; application_budget also enforces lifetime/time.
MAINTENANCE = re.compile(r"maintenance|temporarily unavailable|scheduled outage|service unavailable", re.I)


# Automatic retries of a job stop after this many runs in a row that ended on the same blocker under the same build:
# runs 4+ took 63% of 72 hours of worker minutes for 7-15% confirmations, and automatic retries confirmed none of 93
# (2026-10-29). A new build, a different blocker or the owner's answer or Apply (a new run in between) starts it afresh.
SAME_STALL_RUNS = 3


def stall_key(reason):
    """A run's blocker without the numbers (counts, field indexes, durations) that differ between identical stalls."""
    return re.sub(r"\d+", "#", (reason or "").strip().lower())[:80]


def persist_wait(runs_today, reason):
    seconds = min(60, 30 * max(1, runs_today))
    if MAINTENANCE.search(reason or ""):
        seconds = max(seconds, 3600)
    return timedelta(seconds=seconds)


UNKNOWN_PER_TICK = 1


def unknown_candidates(db):
    """(job id, uncertain run id) for uncertain submissions the resubmit policy allows, oldest attempt first."""
    from .admission import unknown_resubmit_allowed
    from .reserved import held

    with db.session() as s:
        found = []
        waiting = held(s)  # A resubmit applies again: reserved employers wait for the owner (2026-10-27).
        for job in s.scalars(select(Job).where(Job.status == "submission_unknown")):
            if job.id in waiting:
                continue
            runs = list(s.scalars(select(Run).where(Run.job_id == job.id).order_by(Run.created_at.desc())))
            if runs and runs[0].state == "submission_unknown" and unknown_resubmit_allowed(s, runs):
                found.append((runs[0].created_at, job.id, runs[0].id))
    return [(job_id, run_id) for _, job_id, run_id in sorted(found)]


async def resubmit_unknown(service, slots):
    """Owner 2026-10-12: "Don't protect unknown submissions, resubmit them till you get a confirmation."

    Only under `unknown_resubmit_allowed` (no receipt, the receipt wait passed, at most two resubmits), and only when
    the uncertain run's saved final page shows no confirmation. A page that thanks the applicant is triaged as
    submitted (`unknown_triage:<job>`) and left to the owner's one-click reconcile, never resubmitted; a run without a
    saved final page is never resubmitted blind. Returns how many were queued."""
    from pathlib import Path

    from .admission import QueueBlocked
    from .forms import EMPLOYER_CONFIRMATION_RE
    from .repair_agent import CONFIRMATION

    if slots <= 0:
        return 0
    db, queued = service.db, 0
    for job_id, run_id in await asyncio.to_thread(unknown_candidates, db):
        if queued >= slots:
            break
        try:
            page = await asyncio.to_thread((Path(service.config.data_dir) / "runs" / run_id / "final_outline.txt").read_text)
        except OSError:
            continue
        # Either vocabulary: Asana's "Task complete! Thank you for sharing your information" (10-18) is a submission.
        if CONFIRMATION.search(page) or EMPLOYER_CONFIRMATION_RE.search(page):
            db.set_setting("unknown_triage:" + job_id, {"verdict": "submitted", "run_id": run_id, "at": now(),
                                                         "source": "the uncertain run's final page"})
            db.event(run_id, "unknown_triage", "The final page shows a confirmation; not resubmitted", {})
            continue
        try:
            await service.queue(job_id, "submit")
        except (QueueBlocked, ValueError):
            continue
        db.event(run_id, "unknown_resubmit",
                 "Resubmitting an uncertain application: no receipt, and its final page shows no confirmation", {})
        queued += 1
    return queued


async def persist_stalled(service, slots, roles, batch):
    """Resume stalled, still-open applications into free slots: one per employer, one Eightfold at a time
    (shared code sender), never a job waiting on the owner, dead, closed, out of budget or outside the
    profile's roles. Oldest failure first. Returns how many were started."""
    from .db import Review
    from .features.discovery.roles import matching_role
    from .job_control import manage

    db = service.db
    if slots <= 0:
        return 0
    exclude = set((db.get_setting("ats_focus", {}) or {}).get("exclude", []))
    current = datetime.now(UTC)
    since, day = (current - timedelta(days=14)).isoformat(), (current - timedelta(hours=24)).isoformat()
    with db.session() as s:
        active_rows = s.execute(
            select(Job.company, Job.ats).join(Run, Run.job_id == Job.id).where(Run.state.in_(ACTIVE))
        ).all()
        busy = {(company or "").casefold() for company, _ in active_rows}
        eightfold_busy = any(ats == "eightfold" for _, ats in active_rows)
        latest, today = {}, {}
        # Saved packets are hundreds of KB each; retry scheduling needs only metadata.
        from .application_budget import nothing_sent

        from .recovery import backoff_seconds, nothing_sent_streak, repeats_unchanged

        streaks, history = {}, {}
        for run in s.execute(select(
            Run.id, Run.job_id, Run.state, Run.reason, Run.created_at, Run.finished_at,
            Run.packet["engine_version"].as_string().label("engine"),
        ).where(Run.created_at >= since).order_by(Run.created_at)):
            latest[run.job_id] = run
            # Runs in a row that ended on the same blocker under the same build: a retry with nothing new repeats it.
            key = (run.engine, stall_key(run.reason))
            streak = streaks.get(run.job_id)
            streaks[run.job_id] = (key, streak[1] + 1 if streak and streak[0] == key else 1)
            history.setdefault(run.job_id, []).append(run)
            # Deployment interruptions and sender waits sent nothing; they are not one of the day's retries
            # (Goldman Sachs' fixed job sat out its quota on two interrupted runs, 2026-10-18).
            if run.created_at >= day and not nothing_sent(run.state, run.reason):
                today[run.job_id] = today.get(run.job_id, 0) + 1
        asking = set(s.scalars(select(Review.run_id).where(Review.answer.is_(None))))
        from .reserved import held

        # Reserved employers are not retried until the owner approves them (2026-10-27); queue admission would refuse
        # them anyway, every tick.
        waiting = held(s)
        candidates = []
        for job_id, run in latest.items():
            runs_today = today.get(job_id, 0)
            if (
                run.state not in {"waiting_browser", "timed_out", "needs_review"}
                or job_id in waiting
                or run.id in asking
                or PERSIST_SKIP.search(run.reason or "")
                or runs_today >= PERSIST_DAILY
                or streaks[job_id][1] >= SAME_STALL_RUNS
                or (run.finished_at or run.created_at) > (current - persist_wait(runs_today, run.reason)).isoformat()
            ):
                continue
            # Waits that sent nothing back off 1, 2, 4 ... 30 minutes instead of the flat 30-60 s (a model outage
            # requeued one job eight times in 12 minutes, 2026-10-28); a stall that repeated on this very engine
            # runs the same failing step again, so it waits for a new engine.
            streak = nothing_sent_streak((r.state, r.reason) for r in history[job_id])
            if streak and (run.finished_at or run.created_at) > (
                current - timedelta(seconds=backoff_seconds(streak, 60, 1800))
            ).isoformat():
                continue
            if repeats_unchanged((r.reason, r.engine) for r in history[job_id]):
                continue
            job = s.get(Job, job_id)
            control = s.get(Setting, "job_control:" + job_id)
            if (
                not job
                or job.demo
                or (control and (control.value or {}).get("state") in {"paused", "deleted"})
                or job.ats not in PERSIST_ATS
                or job.ats in exclude
                or job.status in {"archived", "closed", "confirmed", "already_applied", "skipped"}
                or (roles and not matching_role(job.title, roles))
            ):
                continue
            from .application_budget import require_allowance
            from .admission import QueueBlocked

            try:
                require_allowance(s, job_id)
            except QueueBlocked:
                continue
            candidates.append((run.created_at, job.id, (job.company or "").casefold(), job.ats))
    started = 0
    for _, job_id, employer, ats in sorted(candidates):
        if started >= slots:
            break
        if employer in busy or (ats == "eightfold" and eightfold_busy):
            continue
        try:
            await manage(service, job_id, "resume", actor="autopilot")
        except ValueError:
            continue  # Budget, cap or pool limits: the next tick reconsiders it.
        busy.add(employer)
        eightfold_busy = eightfold_busy or ats == "eightfold"
        started += 1
        batch["persisted"] = [*batch.get("persisted", []), {"job_id": job_id, "at": now()}][-200:]
    return started


def dispatch_priority(job, priority="new_grad_first"):
    """Sort key for ready jobs (higher first). The profile's job_priority decides whether new-grad postings
    go ahead of every other job (2026-10-17: the score-only sort had made new grad a mere tiebreaker)."""
    from .features.discovery.feeds import NEW_GRAD

    score = job.get("score") if job.get("score") is not None else -1
    level, title = job.get("career_level") or "", job.get("title") or ""
    explicit = re.search(
        r"\bnew[ -]?(?:college[ -]?)?grad|\buniversity[ -]?(?:grad|hire)|\brecent[ -]?grad|\bcampus\b|"
        r"\bclass of 20\d\d|\b20\d\d (?:start|grad)",
        title,
        re.I,
    )
    # Explicit new-grad postings, then entry-level ones (Engineer I, Junior, early career), then the rest.
    tier = 2 if level == "New grad" or explicit else 1 if level == "Early career" or NEW_GRAD.search(title) else 0
    if priority == "best_fit_first":
        return (score, tier, job.get("posted_at") or "")
    return (tier, score, job.get("posted_at") or "")


def admission_revision(db, cfg):
    """Only policy that can change a Ready job's admission, without applicant facts."""
    from .application_pacing import COMPANY_LIMIT, LIMIT_KEY

    policy = {
        "company_limit": int(db.get_setting(LIMIT_KEY, {}).get("limit") or COMPANY_LIMIT),
        "target_roles": sorted(db.get_setting("profile", {}).get("target_roles") or []),
        "admit_by_title": bool(db.get_setting("profile", {}).get("admit_by_title")),
        "excluded_ats": sorted(db.get_setting("ats_focus", {}).get("exclude") or []),
        "minimum_auto_fit": cfg.minimum_auto_fit,
        # readmit_by_threshold's upper bound: lowering the profile's fit must revisit the jobs it ranked skipped.
        "minimum_fit": db.get_setting("profile", {}).get("minimum_fit"),
    }
    return hashlib.sha256(json.dumps(policy, sort_keys=True).encode()).hexdigest()[:16]


def open_repairs(db):
    """Jobs in the dashboard's Repairing group: their latest run waits for the browser, timed out or failed.

    Not a reserved employer's job awaiting the owner's approval: it shows in Jobs › Reserved and nothing retries it, so
    it would hold every new application until the owner approved it (2026-10-27)."""
    from .api.pipeline import RUN_GROUPS
    from .reserved import held

    with db.session() as s:
        waiting = list(held(s))
        latest = select(Run.job_id, func.max(Run.created_at).label("at")).group_by(Run.job_id).subquery()
        return s.scalar(
            select(func.count())
            .select_from(Run)
            .join(latest, (Run.job_id == latest.c.job_id) & (Run.created_at == latest.c.at))
            .join(Job, Job.id == Run.job_id)
            .where(
                Run.state.in_(RUN_GROUPS["repair"]),
                Job.status != "archived",
                *([Job.id.not_in(waiting)] if waiting else []),
            )
        ) or 0


def new_applications_held(db):
    """Nothing holds new applications (owner 2026-10-28: "remove all the stupid allowances ... no blockages till I tell
    you"). The repairs-first hold and the new-application allowance are gone; repairs and new jobs run side by side."""
    return 0


async def owner_requests_only(service):
    """Start the owner's deferred Apply clicks as admission allows, when no autopilot batch is running."""
    from .owner_apply import due
    from .pipeline_comparison import NORMAL, pool_capacity

    with service.db.session() as s:
        room = pool_capacity(s, NORMAL, service.config.topology).available
    return await due(service, room)


def classification_ids(due_work, attempted, limit=None):
    """The planner's unclassified inventory to rank next, minus jobs already attempted.

    It was also limited to postings re-seen in the last five hours. Discovery pauses while unclassified inventory is
    high, so no posting was re-seen: 5 of 53 planned jobs passed, classification ran about 10 an hour, and Workday's
    1,216 sources went unscanned for 41 hours (2026-10-25). The planner already excludes gated, skipped and cooling
    jobs."""
    from .features.discovery.policy import DiscoveryPolicy

    # The planner returns up to three model batches a tick; a 24-job cap here dropped two thirds of its plan.
    limit = limit or DiscoveryPolicy().classify_batch * 3
    return [jid for group in due_work.classify for jid in group if jid not in attempted][:limit]


def unlisted_ready(db, cfg, listed):
    """Ranked-ready jobs with no live discovery listing: postings imported directly (job board APIs, pasted links)
    and listings a sweep has not re-seen yet. 122 such jobs above the fit threshold sat unseen while six of eight
    workers idled (2026-10-24). Each was added within the board's age window and has never been attempted; the
    dispatch guards (roles, discovery gate, fit, employer limits) still decide.

    A listing the board left out (not re-seen in six hours) still knows the posting's country, level and place: without
    them the discovery gate read "Remote-MO" with no country as non-US and refused 24 ready jobs for good (2026-10-29).
    A listing its source marked inactive means the posting was taken down; that job is not a candidate."""
    from .db import JobListing

    cutoff = (datetime.now(UTC) - timedelta(hours=cfg.max_age_hours)).isoformat()
    with db.session() as s:
        rows = s.execute(
            select(
                Job.id, Job.title, Job.company, Job.location, Job.status, Job.score, Job.created_at, Job.ats,
                JobListing.location.label("listing_location"), JobListing.country, JobListing.career_level,
                JobListing.active,
            )
            .outerjoin(JobListing, JobListing.job_id == Job.id)
            .where(
                Job.status == "ready",
                Job.demo.is_(False),
                Job.created_at >= cutoff,
                Job.id.not_in(select(Run.job_id)),
            )
        ).all()
    return [
        {
            "id": row.id,
            "title": row.title,
            "company": row.company,
            "location": row.listing_location or row.location,
            "country": row.country or "",
            # The lane (dispatch_ready: custom_site) reads the ATS: a row without one counted as a custom site, so every
            # listing-less job waited for the single custom-site worker, or for ever when it is off (2026-10-29).
            "ats": row.ats,
            "status": row.status,
            "score": row.score,
            "posted_at": row.created_at or "",
            "career_level": row.career_level or "Imported",
        }
        for row in rows
        if row.id not in listed and row.active is not False
    ]


async def tick(service):
    from .discovery import title_admits

    db, cfg = service.db, settings(service.db)
    control = db.get_setting("control", {})
    if (
        control.get("paused")
        or not control.get("auto_submit")
        or not service.dispatch
    ):
        return
    if not hasattr(service, "autopilot_lock"):
        service.autopilot_lock = asyncio.Lock()
    if not cfg.enabled or not cfg.auto_apply:
        # The owner's own Apply clicks never depend on the autopilot. With auto-apply off, a click deferred by pacing
        # or a busy mail sender was promised "it starts on its own" and never did (2026-10-21).
        async with service.autopilot_lock:
            await owner_requests_only(service)
        return
    async with service.autopilot_lock:
        batch = db.get_setting("job_board_autopilot") or start_batch(db, cfg.batch_target)
        if batch.get("state") != "active":
            await owner_requests_only(service)
            return
        with db.session() as s:
            owned_jobs = select(Run.job_id).where(Run.id.in_(batch["run_ids"]))
            # Every confirmation since the batch started counts toward its goal, whoever started it (Apply, bulk
            # Apply): the dashboard counted 436 against a goal the tick measured as 222 (2026-10-29).
            confirmed = len(
                set(
                    s.scalars(
                        select(Run.job_id).distinct().where(
                            Run.job_id.in_(owned_jobs),
                            Run.state == "confirmed",
                            Run.receipt["evidence_reconciliation"].as_string().is_distinct_from("pending"),
                        )
                    )
                )
                | confirmed_since(s, batch.get("started_at"))
            )
            # A count: the whole rows (packets and descriptions) were read here every 15 s only to be counted.
            active = s.scalar(
                select(func.count())
                .select_from(Run)
                .join(Job, Run.job_id == Job.id)
                .where(Run.state.in_(ACTIVE))
            )
            slots = capacity(s, service.config.workers)
            occupied_slots = slots.occupied
            from .pipeline_comparison import CUSTOM, NORMAL, pool_capacity

            normal_room = pool_capacity(s, NORMAL, service.config.topology).available
            # Custom-site jobs take only the custom-site worker's slot (Topology.custom), never an ATS worker's.
            custom_room = pool_capacity(s, CUSTOM, service.config.topology).available
            today = datetime.now(ZoneInfo(service.config.timezone)).date().isoformat()
            budget = s.get(Budget, today)
            available_submissions = service.config.daily_application_limit - (
                budget.submissions if budget else 0
            )
        batch.update(confirmed=confirmed, last_tick=now())
        if confirmed >= batch["target"]:
            batch.update(state="complete", finished_at=now())
            persist_batch(db, batch)
            return
        from .provider_health import dispatch_gate

        gate = dispatch_gate(db, service.config)
        provider_room = gate.limit
        batch["waiting_for_provider"] = provider_room == 0 and gate.code == "provider_unavailable"
        # Not the model: the worker Chrome is gone or the server is stopping (host_health.check).
        batch["held_by_host"] = gate.code if provider_room == 0 and gate.code != "provider_unavailable" else ""
        if not provider_room:
            persist_batch(db, batch)
            return
        # In-flight jobs might all succeed. Never overrun the user's requested success target.
        room = max(
            0,
            min(
                normal_room + custom_room,
                provider_room - occupied_slots,
                batch["target"] - confirmed - active,
                available_submissions - active,
            ),
        )
        batch["waiting_for_daily_limit"] = available_submissions <= active
        considered = set(batch["considered_ids"])
        deferred = dict(batch.get("deferred", {}))
        # The dashboard page limit must not exclude the planner's fresh jobs.
        # Full-board scans and readiness scoring are read-only; run them off the
        # event loop that every browser owner's short deadlines depend on.
        board = await asyncio.to_thread(board_snapshot, db, limit=None)
        revision = admission_revision(db, cfg)
        if db.get_setting("title_readmit_revision", {}).get("revision") != revision:
            from .discovery import readmit_by_title

            count = await asyncio.to_thread(readmit_by_title, db, [j["id"] for j in board["jobs"]])
            # Jobs ranked skipped only by the profile's higher minimum_fit, once per policy revision like the titles.
            from .discovery import readmit_by_threshold

            count += await asyncio.to_thread(readmit_by_threshold, db)
            db.set_setting("title_readmit_revision", {"revision": revision, "readmitted": count, "at": now()})
            if count:
                board = await asyncio.to_thread(board_snapshot, db, limit=None)
        from .execution_services import enabled as services_enabled, master_policy

        integrated = services_enabled(db)
        use_master = bool(master_policy(db))
        manual = set()
        if integrated:
            from .features.discovery import policy_for, ready_applications

            listed = {j["id"] for j in board["jobs"]}
            if use_master:
                from .features.discovery.readiness import ready_manual_job_ids

                manual = await asyncio.to_thread(ready_manual_job_ids, db, policy_for(db))
                manual -= listed
            else:
                ready = await asyncio.to_thread(ready_applications, db, policy_for(db), limit=200)
                manual = {
                    app.job_id for app in ready if app.posted_basis == "manual" and app.job_id not in listed
                }
            with db.session() as s:
                for row in s.scalars(select(Job).where(Job.id.in_(manual))):
                    if db.get_setting("job_origin:" + row.id, {}).get("kind") != "manual":
                        continue
                    board["jobs"].append(
                        {
                            "id": row.id,
                            "title": row.title,
                            "company": row.company,
                            "location": row.location,
                            "status": row.status,
                            "score": row.score,
                            "ats": row.ats,
                            "posted_at": "",
                            "career_level": "Manual link",
                        }
                    )
        board["jobs"].extend(await asyncio.to_thread(unlisted_ready, db, cfg, {j["id"] for j in board["jobs"]}))
        board_ids = {j["id"] for j in board["jobs"]}
        considered_state = batch.setdefault("considered_state", {})
        policy_revision = admission_revision(db, cfg)
        # A rejection is looked at again every RECONSIDER_SECONDS: a job turned away while a pool, an employer or a
        # hold was briefly full was never considered again (222 ready jobs under every limit sat untried, 2026-10-29).
        window = int(time.time() // RECONSIDER_SECONDS)

        def admission_state(job):
            return f"{job.get('status')}:{job.get('score')}:{policy_revision}:{window}"

        def reconsider(job):
            # A job considered while unranked or skipped and later ranked ready
            # (for example after a classification repair) must not be excluded
            # forever. Changed owner limits also invalidate old rejections.
            # Each changed admission state is considered exactly once; queue's
            # live guards and the attempted filter still protect prior submits.
            return job.get("status") == "ready" and considered_state.get(job["id"]) != admission_state(job)

        jobs = [
            j
            for j in board["jobs"]
            if (j["id"] not in considered or reconsider(j))
            and deferred.get(j["id"], {}).get("not_before", "") <= now()
        ]
        from .schemas import Profile

        owner_profile = Profile.model_validate(db.get_setting("profile", {}) or {})
        priority, target_roles = owner_profile.job_priority, owner_profile.target_roles
        jobs.sort(key=lambda j: (j["career_level"] == "New grad", j["posted_at"]), reverse=True)
        if not room:
            persist_batch(db, batch)
            return
        from .reserved import held_ids

        with db.session() as s:
            attempted = set(s.scalars(select(Run.job_id)))
        # FAANG, MANGO and the Big Four wait for the owner's approval (2026-10-27): never a candidate, so they never take
        # a slot. Queue admission refuses them too, whoever asks.
        reserved = await asyncio.to_thread(held_ids, db)
        candidates = [j for j in jobs if j["id"] not in attempted and j["id"] not in reserved]
        # Repairs first: fresh postings wait while any application is repairing (owner 2026-10-27); retries, uncertain
        # submissions and the owner's own Apply clicks continue below.
        holding = new_applications_held(db)
        batch["holding_new_for_repairs"] = holding
        if holding:
            candidates = []
        replenish_discovery(service, len(candidates))
        if integrated:
            from .execution_services import prepare_due

            due_work = await prepare_due(service)
        # Dispatch ready jobs first. A slow or malformed ranking response cannot
        # block already classified work from using the independent browser slots.
        jobs = list(candidates)
        jobs.sort(key=lambda j: dispatch_priority(j, priority), reverse=True)
        with db.session() as s:
            occupied_employers = set(capacity(s, service.config.workers).employers)
        queued_now = 0
        rooms = {"custom": custom_room, "normal": normal_room}

        def screen(job, busy):
            """The job's admission screen, on a worker thread: its row, the profile's roles, fit, the discovery gate and
            the tailored resume. Every step used to read the database on the event loop that drives the browsers, once per
            job, for each of the ~1,100 Ready jobs a reconsideration window brings back (2026-10-29).
            Returns ("previous" | "focus" | "busy" | "tailoring", employer, None) or ("ok", employer, artifact_id);
            a rejection raises ValueError."""
            jid = job["id"]
            with db.session() as s:
                if s.scalar(select(Run.id).where(Run.job_id == jid)):
                    return "previous", None, None
                current = s.get(Job, jid)
                # Owner focus: platforms set aside for now are not dispatched.
                if current.ats in set(db.get_setting("ats_focus", {}).get("exclude", [])):
                    return "focus", None, None
                from .features.discovery.roles import matching_role

                if target_roles and not matching_role(current.title, target_roles):
                    # Ranked before the profile's roles changed (2026-10-17); its search filters apply here too.
                    raise ValueError("Outside the profile's target roles: " + current.title[:120])
                employer = employer_partition(current)
                if employer in busy:
                    return "busy", employer, None  # Reconsider later; another employer can use this slot.
                if current.status != "ready":
                    raise ValueError(current.reason or "Resume fit did not meet your saved criteria")
                if (current.score or 0) < cfg.minimum_auto_fit and not title_admits(
                    owner_profile, current.title, current.reason
                ):
                    raise ValueError(
                        f"Resume fit {current.score or 0}/100 is below the unattended threshold {cfg.minimum_auto_fit}"
                    )
                # The board these jobs came from, refreshed whenever it is read again below: a job the second read
                # added was refused "aged out" by the first read's list (2026-10-29).
                if jid not in board_ids:
                    raise ValueError("Posting aged out before submission")
                if not integrated:
                    return "ok", employer, None
                from .features.discovery import gate, policy_for

                data = {
                    **job,
                    "url": current.url,
                    "title": current.title,
                    "location": current.location,
                    "description": current.description,
                }
                if not data.get("country"):
                    # A posting with no listing says only "Remote" or "2 Locations": no place is not a foreign place. The
                    # ranker and the board import already screened it; only a stated foreign location refuses it, as the
                    # owner's own Apply does (22 Ready jobs were refused non_us_or_unknown for good, 2026-10-29).
                    data["country"] = "" if FOREIGN.search(current.location or "") else "US"
                if reason := gate(data, policy_for(db)):
                    raise ValueError(reason)
            if use_master:
                return "ok", employer, None
            from .execution_services import ready_for_job

            artifact = ready_for_job(db, jid, service.config.data_dir)
            # Tailoring owns its own durable queue; no browser slot is held.
            return ("ok", employer, artifact.artifact_id) if artifact else ("tailoring", employer, None)

        async def dispatch_ready(jobs):
            nonlocal queued_now
            from .pipeline_comparison import custom_site

            for job in jobs:
                if queued_now >= room:
                    break
                lane = "custom" if custom_site(job) else "normal"
                if rooms[lane] <= 0:
                    continue  # That pool is full (or the custom-site worker is off); the other may still have room.
                if job.get("score") is None and job["status"] != "ready":
                    continue
                jid = job["id"]
                try:
                    verdict, employer, artifact_id = await asyncio.to_thread(screen, job, frozenset(occupied_employers))
                    if verdict != "ok":
                        if verdict == "previous":
                            # This batch never blindly retries jobs from earlier testing or unknown commits.
                            batch["considered_ids"].append(jid)
                            considered_state[jid] = admission_state(job)
                            remember_rejection(batch, jid, "An earlier application exists for this job", "previous_run")
                        else:
                            # Reconsidered next tick; the reason stays visible meanwhile.
                            remember_rejection(batch, jid, WAITING[verdict], verdict)
                        continue
                    run = await service.queue(
                        jid,
                        "submit",
                        batch_id=batch["started_at"],
                        **({"resume_artifact_id": artifact_id} if artifact_id else {}),
                    )
                    batch["run_ids"].append(run["id"])
                    considered.add(jid)
                    batch["queued"] += 1
                    queued_now += 1
                    rooms[lane] -= 1
                    occupied_employers.add(employer)
                    deferred.pop(jid, None)
                    batch.get("rejections", {}).pop(jid, None)
                except (ValueError, RuntimeError) as exc:
                    outcome = outcome_for(exc)
                    remember_rejection(batch, jid, str(exc), outcome["code"], outcome["outcome"])
                    if outcome["outcome"] == "deferred":
                        from .owner_apply import FREES_UP

                        if outcome["code"] not in FREES_UP:
                            deferred[jid] = {**outcome, "reason": str(exc)[:240]}
                        if outcome["code"] == "worker_capacity":
                            rooms[lane] = 0  # The pool filled meanwhile: asking again for each remaining job only costs locks.
                        continue
                    batch["skipped"] = [
                        *batch["skipped"],
                        {"job_id": jid, "reason": str(exc)[:240], **outcome},
                    ][-100:]
                except Exception as exc:
                    # Transient model/transport failures can be reconsidered on the next scheduled tick.
                    batch["last_error"] = type(exc).__name__
                    await asyncio.to_thread(persist_batch, db, batch)
                    continue  # Failure of one job never stops dispatching other jobs.
                batch["considered_ids"].append(jid)
                considered_state[jid] = admission_state(job)
                batch["deferred"] = deferred
            # One write per pass: the whole batch (~225 KB) was rewritten after every one of ~1,100 refused jobs.
            batch["deferred"] = deferred
            await asyncio.to_thread(persist_batch, db, batch)

        from .owner_apply import due as owner_requests

        # The owner's own Apply clicks go first; each one waits only for admission to allow it. Retries of earlier
        # attempts wait for the slots fresh ready postings leave (2026-10-29; before, up to half of the free slots).
        fresh = any(j.get("status") == "ready" for j in jobs)
        free = room - queued_now
        # Retries wait for what no fresh job can use: first attempts confirm two in three, a third run one in eight.
        queued_now += await owner_requests(service, free, retries=0 if fresh else None)
        await dispatch_ready(jobs)
        unranked = [j["id"] for j in candidates[:24] if j.get("score") is None and j["status"] != "ready"]
        if integrated:
            unranked = classification_ids(due_work, attempted)
        if queued_now < room and unranked and not holding:
            try:
                if integrated:
                    from .workers import enqueue_classification

                    await enqueue_classification(unranked)
                else:
                    await rank_batch(db, service.config, unranked)
                batch.pop("last_error", None)
                fresh_board = (await asyncio.to_thread(board_snapshot, db, limit=None))["jobs"]
                board_ids.update(j["id"] for j in fresh_board)
                jobs = [
                    j
                    for j in fresh_board
                    if (j["id"] not in considered or reconsider(j))
                    and j["id"] not in attempted
                    and j["id"] not in reserved
                    and deferred.get(j["id"], {}).get("not_before", "") <= now()
                ]
                jobs.sort(key=lambda j: dispatch_priority(j, priority), reverse=True)
                await dispatch_ready(jobs)
            except Exception as exc:
                batch["last_error"] = type(exc).__name__
        if queued_now < room:
            try:
                queued_now += await persist_stalled(service, room - queued_now, target_roles, batch)
            except Exception as exc:
                batch["last_error"] = type(exc).__name__
        if queued_now < room:
            try:
                queued_now += await resubmit_unknown(service, min(UNKNOWN_PER_TICK, room - queued_now))
            except Exception as exc:
                batch["last_error"] = type(exc).__name__
        if queued_now < room:
            try:
                # The owner's deferred Apply clicks (iCIMS/SuccessFactors pacing, a busy sender) start while a batch runs
                # too, in the slots it leaves free. They used to wait until the batch ended: with the batch's own
                # candidates exhausted, 57 iCIMS/SuccessFactors clicks sat while workers idled (2026-10-21).
                await owner_requests_only(service)
            except Exception as exc:
                batch["last_error"] = type(exc).__name__
        batch["waiting_for_jobs"] = not (active or queued_now) and not any(
            j["id"] not in set(batch["considered_ids"]) for j in jobs
        )
        persist_batch(db, batch)
