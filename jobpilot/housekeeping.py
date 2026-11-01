"""What is kept, and for how long (owner 2026-10-31: "no point of keeping information about older jobs that we never
applied to, and no point of keeping older logs than a week").

- A job past JOB_RETENTION_DAYS with no application behind it goes, with every row and file tied to it. A job still listed
  by its board stays, since the next sweep would only add it back. READY_RETENTION_DAYS gives a job waiting its turn longer:
  19% of confirmed applications start between 7 and 21 days after discovery.
- Past LOG_RETENTION_DAYS a run's logs go: step files, images and detail events. A run that reached the employer keeps its
  final page and what it answered, which are the application's record and not its log. One that did not keeps nothing.

What never goes is what duplicate protection and open work read: a job with any confirmed, uncertain or attempted
submission (the "submitting" event precedes the click), a job whose latest run is at work, one the owner paused,
requested or holds as reserved, and one with a recovery scheduled. A commit that cannot be ruled out counts as sent.
"""

import asyncio
import logging
import re
import shutil
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy import delete, func, select

from .db import Event, Job, JobListing, MailChallenge, Outbox, Review, Run, Setting, now

log = logging.getLogger("jobpilot.housekeeping")
SETTING = "housekeeping"
FIRST_DELAY_SECONDS = 1200  # Never part of a start's load.
INTERVAL_SECONDS = 86_400
BATCH = 100
PAUSE = 0.05

REMOVABLE = {"new", "skipped", "ready", "closed", "archived", "cancelled", "failed", "waiting_answer", "needs_review"}
# A run in one of these, or one that logged the commit step, may have reached the employer.
SENT = {"confirmed", "already_applied", "submission_unknown", "submitting"}
# A job whose latest run is here is open work, whatever its status says.
OPEN = {"queued", "running", "submitting", "waiting_email", "waiting_answer", "waiting_browser", "needs_review", "paused"}
# Open work that only the owner can move. A question left unanswered past the job's age is not coming back ("no point of
# keeping information about older jobs that we never applied to"): nothing was sent, so the job goes like any other.
UNANSWERED = {"waiting_answer", "needs_review"}
# Read for old runs by receipts, recovery, admission and the iCIMS driver.
KEPT_EVENTS = ("submitting", "finished", "form_post", "browser")
RECORD_FILES = {"final.png", "answers.json", "final_outline.txt"}
RUN_FOLDER = re.compile(r"[0-9a-f]{32}")
JOB_TABLES = ("dc_classifications", "dc_deferrals", "dc_requisitions", "rs_artifacts")


def _cutoff(days, at):
    return (at - timedelta(days=days)).isoformat()


def _protected(session, *, removals=False):
    """Job ids the owner or a scheduled recovery still holds. `removals`: also those the owner removed, whose row is the
    verdict: gone, the next sweep of its board adds the posting back as new."""
    from .reserved import held

    keep = set(held(session))
    for key, value in session.execute(
        select(Setting.key, Setting.value).where(
            sa.or_(
                Setting.key.startswith("job_control:"),
                Setting.key.startswith("owner_apply:"),
                Setting.key.startswith("draft_recovery:"),
            )
        )
    ):
        prefix, _, job_id = key.partition(":")
        if prefix == "owner_apply" or (isinstance(value, dict) and (value.get("state") == "paused" or value.get("scheduled") or (removals and value.get("state") == "deleted"))):
            keep.add(job_id)
    return keep


def _facts(session):
    """(runs per job, oldest first, as (id, state, created_at)), and the runs that logged a commit."""
    runs = {}
    for run_id, job_id, state, created in session.execute(
        select(Run.id, Run.job_id, Run.state, Run.created_at).order_by(Run.created_at)
    ):
        runs.setdefault(job_id, []).append((run_id, state, created))
    committed = set(session.scalars(select(Event.run_id).where(Event.kind == "submitting")))
    return runs, committed


def _sent(run, committed):
    return run[1] in SENT or run[0] in committed


def _setting_keys(session):
    """{job or run id: its per-item setting keys}: "job_origin:<id>", "failure_report:<id>" ..."""
    keys = {}
    for key in session.scalars(select(Setting.key).where(Setting.key.contains(":"))):
        suffix = key.rsplit(":", 1)[1]
        if RUN_FOLDER.fullmatch(suffix):
            keys.setdefault(suffix, []).append(key)
    return keys


def _size(path):
    total = 0
    for item in path.rglob("*"):
        try:
            if item.is_file() and not item.is_symlink():
                total += item.stat().st_size
        except OSError:
            pass
    return total


def _remove_folder(runs_root, run_id, apply):
    folder = Path(runs_root) / run_id
    if not RUN_FOLDER.fullmatch(run_id) or folder.is_symlink() or not folder.is_dir():
        return 0
    size = _size(folder)
    if apply:
        shutil.rmtree(folder, ignore_errors=True)
    return size


def _trim_folder(runs_root, run_id, apply):
    """Everything in a sent run's folder but its record."""
    folder = Path(runs_root) / run_id
    if not RUN_FOLDER.fullmatch(run_id) or folder.is_symlink() or not folder.is_dir():
        return 0
    freed = 0
    for item in folder.iterdir():
        if item.name in RECORD_FILES:
            continue
        try:
            if item.is_dir() and not item.is_symlink():
                freed += _size(item)
                if apply:
                    shutil.rmtree(item, ignore_errors=True)
            else:
                freed += item.stat().st_size
                if apply:
                    item.unlink(missing_ok=True)
        except OSError:
            pass
    return freed


def stale_jobs(session, runs, committed, job_cutoff, ready_cutoff, listing_cutoff):
    """Job ids with no application behind them, past their age."""
    protected = _protected(session, removals=True)
    listed = set(
        session.scalars(select(JobListing.job_id).where(JobListing.active.is_(True), JobListing.last_seen >= listing_cutoff))
    )
    stale = []
    for job_id, status, created in session.execute(
        select(Job.id, Job.status, Job.created_at).where(
            Job.demo.is_(False), Job.status.in_(REMOVABLE), Job.created_at < max(job_cutoff, ready_cutoff)
        )
    ):
        if created >= (ready_cutoff if status == "ready" else job_cutoff):
            continue
        history = runs.get(job_id, [])
        if job_id in protected or job_id in listed or any(_sent(run, committed) for run in history):
            continue
        if history and history[-1][1] in OPEN and not (history[-1][1] in UNANSWERED and history[-1][2] < job_cutoff):
            continue
        stale.append(job_id)
    return stale


def _delete_jobs(db, job_ids, keys, tables, committed):
    """One batch, re-read under the lock: a job that has started since is left alone. Returns (jobs, runs) removed."""
    with db.exclusive() as s:
        rows = list(s.execute(select(Run.id, Run.job_id, Run.state).where(Run.job_id.in_(job_ids)).order_by(Run.created_at)))
        latest = {job: state for _, job, state in rows}
        busy = {job for run, job, state in rows if state in SENT or run in committed}
        busy |= {job for job, state in latest.items() if state in OPEN - UNANSWERED}
        jobs = [job for job in job_ids if job not in busy]
        runs = [run for run, job, _ in rows if job not in busy]
        if runs:
            s.execute(delete(Event).where(Event.run_id.in_(runs)))
            s.execute(delete(Review).where(Review.run_id.in_(runs)))
            s.execute(delete(Outbox).where(Outbox.run_id.in_(runs)))
            s.execute(delete(MailChallenge).where(MailChallenge.run_id.in_(runs)))
            s.execute(delete(Run).where(Run.id.in_(runs)))
        for name in tables:
            table = sa.table(name, sa.column("job_id"))
            s.execute(delete(table).where(table.c.job_id.in_(jobs)))
        s.execute(delete(JobListing).where(JobListing.job_id.in_(jobs)))
        doomed = [key for item in [*jobs, *runs] for key in keys.get(item, ())]
        if doomed:
            s.execute(delete(Setting).where(Setting.key.in_(doomed)))
        s.execute(delete(Job).where(Job.id.in_(jobs)))
    return jobs, runs


def sweep(db, data_dir, *, job_days=7, ready_days=14, log_days=7, apply=False, stop=None, at=None):
    """Remove what has outlived its use. A dry run counts the same things and changes nothing. Safe beside live applications."""
    at = at or datetime.now(UTC)
    runs_root, archive = Path(data_dir) / "runs", Path(data_dir) / "archive"
    report = {
        "applied": apply, "jobs_removed": 0, "runs_removed": 0, "logs_trimmed": 0, "events_removed": 0, "orphans_removed": 0,
        "bytes_freed": 0, "stopped_early": False,
    }
    stopping = stop or (lambda: False)
    with db.session() as s:
        runs, committed = _facts(s)
        keys = _setting_keys(s)
        stale = []
        if job_days:
            cut = _cutoff(job_days, at)
            stale = stale_jobs(s, runs, committed, cut, _cutoff(max(ready_days, job_days), at), cut)
        known = set(s.scalars(select(Run.id)))

    tables = [name for name in JOB_TABLES if sa.inspect(db.engine).has_table(name)]
    for start in range(0, len(stale), BATCH):
        if stopping():
            report["stopped_early"] = True
            return report
        batch = stale[start : start + BATCH]
        if apply:
            jobs, gone = _delete_jobs(db, batch, keys, tables, committed)
            time.sleep(PAUSE)
        else:
            jobs, gone = batch, [run[0] for job in batch for run in runs.get(job, [])]
        report["jobs_removed"] += len(jobs)
        report["runs_removed"] += len(gone)
        for run_id in gone:
            report["bytes_freed"] += _remove_folder(runs_root, run_id, apply)
        for job in jobs:
            log_file = archive / f"{job}.jsonl.gz"
            if apply and log_file.is_file():
                log_file.unlink(missing_ok=True)

    if not log_days:
        return report
    cutoff = _cutoff(log_days, at)
    removed = set(stale)
    with db.session() as s:
        held = _protected(s)
    # Recovery reads a job's latest run. Everything older goes on schedule, also when the job is open or held: 554 runs from
    # 10-07 onward kept their steps, and 46,000 of their detail events stayed, because a later attempt was waiting (2026-11-01).
    aged = [
        run
        for job, history in runs.items()
        if job not in removed
        for run in (history[:-1] if job in held or history[-1][1] in OPEN else history)
        if run[2] < cutoff
    ]
    for start in range(0, len(aged), BATCH):
        if stopping():
            report["stopped_early"] = True
            return report
        batch = aged[start : start + BATCH]
        ids = [run[0] for run in batch]
        detail = sa.and_(Event.run_id.in_(ids), Event.kind.not_in(KEPT_EVENTS))
        with db.session() as s:
            if apply:
                report["events_removed"] += s.execute(delete(Event).where(detail)).rowcount or 0
            else:
                report["events_removed"] += s.scalar(select(func.count()).select_from(Event).where(detail)) or 0
        for run in batch:
            sent = _sent(run, committed)
            freed = _trim_folder(runs_root, run[0], apply) if sent else _remove_folder(runs_root, run[0], apply)
            report["bytes_freed"] += freed
            report["logs_trimmed"] += bool(freed)
        if apply:
            time.sleep(PAUSE)

    if runs_root.is_dir():
        limit = (at - timedelta(days=log_days)).timestamp()
        for entry in list(runs_root.iterdir()):
            if stopping():
                report["stopped_early"] = True
                return report
            if not RUN_FOLDER.fullmatch(entry.name) or entry.name in known or entry.is_symlink() or not entry.is_dir():
                continue
            try:
                if entry.stat().st_mtime >= limit:
                    continue
            except OSError:
                continue
            report["bytes_freed"] += _remove_folder(runs_root, entry.name, apply)
            report["orphans_removed"] += 1
    return report


def run(config, db, *, apply=True, stop=None, at=None):
    return sweep(
        db,
        config.data_dir,
        job_days=config.job_retention_days,
        ready_days=config.ready_retention_days,
        log_days=config.log_retention_days,
        apply=apply,
        stop=stop,
        at=at,
    )


async def watch(service):
    """Run daily, off the event loop, and record the outcome where the dashboard can read it."""
    from .host_health import DRAINING

    await asyncio.sleep(FIRST_DELAY_SECONDS)
    while True:
        try:
            report = await asyncio.to_thread(run, service.config, service.db, stop=DRAINING.is_set)
            await asyncio.to_thread(service.db.set_setting, SETTING, {**report, "state": "healthy", "at": now()})
        except Exception as exc:
            log.warning("Housekeeping failed", exc_info=True)
            await asyncio.to_thread(
                service.db.set_setting, SETTING, {"state": "unavailable", "at": now(), "error_type": type(exc).__name__}
            )
        await asyncio.sleep(INTERVAL_SECONDS)
