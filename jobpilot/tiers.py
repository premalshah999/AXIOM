"""Runner tiers: a failed application is retried by progressively stronger runners.

Tier 1 is the standard fast runner. Tier 2 is the same engine with model
reasoning enabled and larger call, step and time budgets. Tier 3 is the
strongest configured runner (optionally another provider, via the
`runner_tiers` setting) with the largest budgets. Escalation never retries a
confirmed, uncertain or submitting application: the queue's own admission rules
(duplicate protection, unknown-submission policy, employer cap) still apply, and
a job is escalated at most once per failed run.
"""

import asyncio
import re
import time
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from .application_budget import nothing_sent
from .db import Event, Job, Run, Setting, now
from .specialist import pool_boost

MAX_TIER = 3
TIERS = {
    1: {"calls": 0, "time": 1.0, "effort": None, "timeout": 0, "tokens": 0},
    2: {"calls": 40, "time": 1.5, "effort": "medium", "timeout": 75, "tokens": 16000},
    3: {"calls": 70, "time": 2.0, "effort": "high", "timeout": 120, "tokens": 24000},
}
FAILED = ("waiting_browser", "timed_out", "failed")
BLOCKED_JOB = {
    "archived",
    "cancelled",
    "paused",
    "confirmed",
    "submission_unknown",
    "already_applied",
    "submitting",
    "closed",
    "skipped",
}
# Failures a stronger runner cannot fix: the posting is gone, or we wait on mail/the owner.
EXTERNAL = re.compile(
    r"closed or no longer|no longer available|does not exist|page not found|job (?:has )?expired|"
    r"verification email|waiting for the employer|already applied|not accepting",
    re.I,
)
COOLDOWN = timedelta(seconds=90)
# Input tokens one job may consume in 24 hours before further runs wait. Failed jobs were
# retried until they had used millions of tokens (a long form costs ~450k per failed run).
JOB_TOKEN_CAP = 1_600_000
# Gates that lift by themselves (host_health.check): the retry waits instead of being marked handled.
TRANSIENT = {
    "worker_capacity",
    "provider_unavailable",
    "tailoring_pending",
    "draining",
    "browser_unavailable",
    "browser_probe",
    "host_overloaded",
    "host_loaded",
}
_cache: dict[str, tuple[int, float]] = {}


def job_tokens(session, job_id, hours=24):
    """Input tokens the model has read for this job's runs in the last `hours`."""
    cutoff = (datetime.now(UTC) - timedelta(hours=hours)).isoformat()
    return float(
        session.scalar(
            select(func.coalesce(func.sum(Event.data["prompt_tokens"].as_float()), 0))
            .select_from(Event)
            .join(Run, Run.id == Event.run_id)
            .where(Run.job_id == job_id, Event.kind == "model_call", Event.created_at > cutoff)
        )
        or 0
    )


def job_over_budget(session, db, job_id, boost=1.0):
    """Has this job read its token cap in the last 24 hours? `boost` scales the cap for a specialist (pool_boost)."""
    cap = ((db.get_setting("runner_tiers", {}) or {}).get("job_token_cap")) or JOB_TOKEN_CAP
    return job_tokens(session, job_id) >= cap * boost


def clamp(tier):
    return tier if tier in TIERS else 1


def tier_of(db, run_id):
    """The tier recorded on a run (cached: a run's tier never changes)."""
    if not run_id:
        return 1
    hit = _cache.get(run_id)
    if hit and time.monotonic() - hit[1] < 3600:
        return hit[0]
    try:
        with db.session() as s:
            value = s.scalar(select(Run.packet["runner_tier"].as_integer()).where(Run.id == run_id))
    except Exception:
        value = None
    tier = clamp(value or 1)
    if len(_cache) > 512:
        _cache.clear()
    _cache[run_id] = (tier, time.monotonic())
    return tier


def job_tier(db, job_id):
    """The tier the next run of this job should use."""
    return clamp((db.get_setting("runner_tier:" + job_id, {}) or {}).get("tier", 1))


def call_limit(config, tier, pool=None):
    """Model calls one run may make; a specialist's run (`pool`) gets SPECIALIST_BUDGET_MULTIPLIER times as many."""
    return int(max(config.max_model_calls, TIERS[clamp(tier)]["calls"]) * pool_boost(config, pool))


def apply_model(db, config, run_id, thinking, timeout):
    """Tier-adjusted (tier, thinking, timeout) for a model call."""
    tier = tier_of(db, run_id)
    if tier >= 2 and config.llm_provider in {"deepseek", "mimo"}:
        thinking = "enabled"
        timeout = max(timeout, TIERS[tier]["timeout"])
    return tier, thinking, timeout


def token_floor(tier):
    """Reasoning shares the completion budget; a truncated reply is an unusable action."""
    return TIERS[clamp(tier)]["tokens"]


def effort_for(tier, default):
    return TIERS[clamp(tier)]["effort"] or default


def provider_for(db, config, tier):
    """A tier may name its own provider (setting `runner_tiers`), when its key is configured."""
    chosen = ((db.get_setting("runner_tiers", {}) or {}).get(str(tier)) or {}).get("provider")
    if chosen in {"mimo", "deepseek", "haiku", "openai", "openrouter"} and getattr(config, chosen + "_api_key", ""):
        return config.model_copy(update={"llm_provider": chosen})
    return config


def time_budget(budget, tier, ats="", boost=1.0):
    # No Workday extension: confirmed Workday runs take 4-5 minutes; a longer budget only let
    # failing runs burn 20 minutes (owner, 2026-10-14). `boost` is a specialist's multiplier (pool_boost).
    return int(min(1200 * boost, budget * TIERS[clamp(tier)]["time"] * boost))


def _candidates(db, limit):
    """Latest failed run per job that a stronger runner may retry."""
    cutoff = (datetime.now(UTC) - COOLDOWN).isoformat()
    excluded = set((db.get_setting("ats_focus", {}) or {}).get("exclude", []))
    out = []
    with db.session() as s:
        from sqlalchemy.orm import aliased

        newer = aliased(Run)
        rows = s.execute(
            select(
                Run.id,
                Run.job_id,
                Run.reason,
                Run.state,
                Run.packet["runner_tier"].as_integer(),
                Run.packet["waiting_kind"].as_string(),
                Job.ats,
                Job.status,
            )
            .join(Job, Run.job_id == Job.id)
            .where(
                Run.state.in_(FAILED),
                Run.mode == "submit",
                Run.finished_at < cutoff,
                Job.demo.is_(False),
                Job.status.notin_(list(BLOCKED_JOB)),
                ~select(newer.id)
                .where(newer.job_id == Run.job_id, newer.created_at > Run.created_at)
                .exists(),
            )
            .order_by(Run.finished_at.desc())
            .limit(max(200, limit * 40))
        ).all()
        for rid, job_id, reason, state, run_tier, waiting, ats, _status in rows:
            tier = clamp(run_tier or 1)
            if ats in excluded or waiting == "email" or EXTERNAL.search(reason or "") or tier >= MAX_TIER:
                continue
            if nothing_sent(state, reason):
                # A deploy, a busy mail sender, a dead Chrome or an overloaded host stopped it before the employer saw
                # anything; a stronger runner (more calls, thinking on) cannot fix that and only spends a tier.
                continue
            if (s.get(Setting, "runner_tier:" + job_id) or Setting(key="", value={})).value.get(
                "from_run"
            ) == rid:
                continue  # Already escalated from this exact failure.
            # Never retry once a submission reached the employer or an outcome is unproven.
            risky = s.scalar(
                select(Run.id)
                .where(
                    Run.job_id == job_id,
                    Run.state.in_(
                        [
                            "queued",
                            "running",
                            "submitting",
                            "confirmed",
                            "submission_unknown",
                            "already_applied",
                        ]
                    ),
                )
                .limit(1)
            ) or s.scalar(
                select(Event.id)
                .join(Run, Run.id == Event.run_id)
                .where(Run.job_id == job_id, Event.kind == "submitting")
                .limit(1)
            )
            if risky:
                continue
            if job_over_budget(s, db, job_id):
                continue
            out.append((rid, job_id, tier))
            if len(out) >= limit:
                break
    return out


async def escalate_due(service, limit=4):
    """Queue the next-tier runner for jobs whose latest run failed. Called once a minute."""
    if (service.db.get_setting("control", {}) or {}).get("paused"):
        return 0
    if (service.db.get_setting("runner_tiers", {}) or {}).get("enabled") is False:
        return 0
    from .job_control import manage

    queued = 0
    for rid, job_id, tier in await asyncio.to_thread(_candidates, service.db, limit):
        key = "runner_tier:" + job_id
        previous = service.db.get_setting(key, None)
        service.db.set_setting(key, {"tier": tier + 1, "from_run": rid, "at": now()})
        try:
            await manage(service, job_id, "resume", actor="tier")
            queued += 1
        except Exception as exc:
            if getattr(exc, "code", None) not in TRANSIENT and hasattr(exc, "code"):
                # A standing protection (employer cap, duplicate protection): this failure
                # is handled, so it cannot starve the jobs behind it. A later run reopens it.
                service.db.set_setting(
                    key, {"tier": tier, "from_run": rid, "blocked": str(exc)[:160], "at": now()}
                )
                continue
            # Capacity, provider cooldown or an unexpected error: retry on a later tick.
            if previous is None:
                with service.db.exclusive() as s:
                    row = s.get(Setting, key)
                    if row:
                        s.delete(row)
            else:
                service.db.set_setting(key, previous)
    return queued
