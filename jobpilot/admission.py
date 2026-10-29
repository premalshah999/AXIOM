"""Shared admission decisions for unattended jobs, manual batches and recovery."""

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from urllib.parse import urlsplit

from sqlalchemy import select

from .db import Job, Run, Setting


class QueueBlocked(ValueError):
    """A queue guard with an explicit permanent/protected/temporary outcome."""

    def __init__(self, message, *, outcome="excluded", code="queue_guard", not_before=None):
        super().__init__(message)
        self.outcome, self.code, self.not_before = outcome, code, not_before

    def as_dict(self):
        return {"outcome": self.outcome, "code": self.code, "not_before": self.not_before}


def require_intake(session, job_id):
    """Every wake path observes the same temporary deployment boundary."""
    row = session.get(Setting, "execution_v2_deployment_hold")
    stage = (row.value if row else {}).get("stage")
    if stage == "canary":
        cohort = session.get(Setting, "execution_v2_canary")
        if job_id in {j["id"] for j in (cohort.value if cohort else {}).get("jobs", [])}:
            return
    if stage in {"draining", "canary"}:
        raise QueueBlocked(
            "Application intake is temporarily held for deployment or a bounded trial",
            outcome="deferred",
            code="deployment_hold",
            not_before=temporary_time(),
        )


# Link shorteners and listing aggregators serve many employers from one host.
SHARED_LINK_HOSTS = {
    "go.vansh.nyc", "bit.ly", "lnkd.in", "tinyurl.com", "t.co", "ow.ly", "buff.ly", "rebrand.ly", "cutt.ly",
    "short.gy", "simplify.jobs", "jobright.ai",
}


def employer_partition(job):
    get = job.get if isinstance(job, dict) else lambda key, default=None: getattr(job, key, default)
    parsed = urlsplit(get("url", ""))
    host = (parsed.hostname or "").lower()
    if host in SHARED_LINK_HOSTS:
        # Every go.vansh.nyc job was one "employer": DV Trading, OneImaging, IXL, ALTEN and Perpay waited for each
        # other as employer_busy, one at a time across all workers (2026-10-29). The employer is the company.
        company = re.sub(r"\s+", " ", str(get("company", "") or "")).strip().casefold()
        return host + "/" + (company or parsed.path.strip("/").lower())
    parts = parsed.path.strip("/").split("/")
    ats = get("ats", "custom")
    if ats in {"greenhouse", "lever", "ashby", "smartrecruiters", "rippling"} and parts[0]:
        return host + "/" + parts[0].lower()
    if ats == "workday" and host.endswith(".myworkdaysite.com") and "recruiting" in parts:
        index = parts.index("recruiting")
        return host + "/" + "/".join(parts[index : index + 3]).lower()
    if host == "jobs.dayforcehcm.com":
        from .features.knowledge.employers import dayforce_tenant

        if tenant := dayforce_tenant([p for p in parts if p]):
            return host + "/" + tenant  # one host serves every Dayforce employer
    return host


@dataclass(frozen=True)
class Capacity:
    running: int
    queued_partitions: frozenset[str]
    running_partitions: frozenset[str]
    reservations: int
    limit: int

    @property
    def occupied(self):
        return self.running + len(self.queued_partitions - self.running_partitions)

    @property
    def available(self):
        return max(0, self.limit - self.occupied)

    @property
    def employers(self):
        return self.running_partitions | self.queued_partitions


def active_runs(session, states=("queued", "running", "submitting"), ats=None):
    """(run, job) pairs for runs in `states`, with only what capacity and pacing decisions read.

    Whole Run and Job rows carried ~320 KB packets and full descriptions; every snapshot, autopilot tick and queue
    click decoded them for each queued and running application, under the app lock (2026-10-22). The packet keeps the
    three keys these checks read (worker pool and slot, verification-only), absent when the run has none.
    """
    query = (
        select(
            Run.id,
            Run.job_id,
            Run.state,
            Run.mode,
            Run.created_at,
            Run.started_at,
            Run.packet["worker_pool"].as_string(),
            Run.packet["worker_slot"].as_integer(),
            Run.packet["verification_only"].as_boolean(),
            Job.url,
            Job.ats,
            Job.company,
            Job.demo,
        )
        .join(Job, Job.id == Run.job_id)
        .where(Run.state.in_(list(states)))
    )
    if ats is not None:
        query = query.where(Job.ats == ats)
    rows = []
    for (
        rid,
        job_id,
        state,
        mode,
        created,
        started,
        pool,
        slot,
        verification,
        url,
        ats_,
        company,
        demo,
    ) in session.execute(query):
        packet = {
            key: value
            for key, value in (
                ("worker_pool", pool),
                ("worker_slot", slot),
                ("verification_only", verification),
            )
            if value is not None
        }
        run = SimpleNamespace(
            id=rid,
            job_id=job_id,
            state=state,
            mode=mode,
            created_at=created,
            started_at=started,
            packet=packet,
        )
        rows.append((run, SimpleNamespace(id=job_id, url=url, ats=ats_, company=company, demo=demo)))
    return rows


def capacity(session, workers=8, rows=None):
    rows = active_runs(session) if rows is None else rows
    running = [(r, j) for r, j in rows if r.state in {"running", "submitting"}]
    return Capacity(
        running=len(running),
        running_partitions=frozenset(employer_partition(j) for _, j in running),
        queued_partitions=frozenset(employer_partition(j) for r, j in rows if r.state == "queued"),
        reservations=len({r.job_id for r, _ in rows}),
        limit=min(32, workers),
    )


def temporary_time(seconds=60):
    return (datetime.now(UTC) + timedelta(seconds=seconds)).isoformat()


def outcome_for(exc):
    if isinstance(exc, QueueBlocked):
        return exc.as_dict()
    # Legacy callers must not turn an unknown transport/runtime guard into permanent loss.
    text = str(exc).lower()
    if any(term in text for term in ("cooldown", "paused", "six hours", "daily", "budget", "active run")):
        return {"outcome": "deferred", "code": "temporary_guard", "not_before": temporary_time()}
    if isinstance(exc, RuntimeError):
        return {"outcome": "deferred", "code": "delivery_failure", "not_before": temporary_time()}
    return {"outcome": "excluded", "code": "queue_guard", "not_before": None}


# Off unless the installation turns it on (setting unknown_resubmit_policy): a resubmission can duplicate an
# application the site did accept.
UNKNOWN_RESUBMIT = {"enabled": False, "max_resubmits": 2, "receipt_wait_minutes": 30}


def unknown_resubmit_allowed(session, runs, at=None):
    """When the installation enables it: retry uncertain submissions until confirmed.

    Bounded so a site that silently accepts cannot collect a pile of duplicates:
    no confirmed/already-applied run, no matched receipt email, the newest
    uncertain attempt is old enough for its receipt to have arrived, and at most
    `max_resubmits` retries after the first uncertain attempt.
    """
    saved = session.get(Setting, "unknown_resubmit_policy")
    policy = {**UNKNOWN_RESUBMIT, **(saved.value if saved else {})}
    unknown = [r for r in runs if r.state == "submission_unknown"]
    if not policy.get("enabled") or not unknown:
        return False
    if any(r.state in {"confirmed", "already_applied", "submitting"} for r in runs):
        return False
    if any(
        (r.receipt or {}).get("email_confirmed") or (r.receipt or {}).get("website_confirmed") for r in runs
    ):
        return False
    if len(unknown) > int(policy.get("max_resubmits", 2)):
        return False
    # A post-submit screenshot that shows the employer's confirmation means the
    # application went through; a receipt or portal check confirms it, never a resubmit.
    triage = (
        session.get(Setting, "unknown_triage:" + unknown[0].job_id) if hasattr(unknown[0], "job_id") else None
    )
    if triage and triage.value.get("verdict") == "submitted":
        return False
    newest = max((r.finished_at or r.created_at) for r in unknown)
    try:
        finished = datetime.fromisoformat(newest)
    except (TypeError, ValueError):
        return False
    now = at or datetime.now(UTC)
    if finished.tzinfo is None:
        finished = finished.replace(tzinfo=UTC)
    return now - finished >= timedelta(minutes=int(policy.get("receipt_wait_minutes", 30)))
