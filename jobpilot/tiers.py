"""Runner tiers: the call, time and reasoning budget a run gets, and the per-job token cap.

Tier 1 is the standard fast runner. Tier 2 and 3 are larger call and time budgets (and, for providers that have one, a
reasoning mode). A job reaches them only through a `runner_tier:<job id>` setting, which nothing writes any more: the
automatic escalation of a failed application to the next tier was removed with the model-led agent it fed (retired
2026-10-14, `runner_tiers.enabled` false since), and the 37 rows it left keep their jobs at the tier they reached.
"""

import time
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from .db import Event, Run
from .specialist import pool_boost

TIERS = {
    1: {"calls": 0, "time": 1.0, "effort": None, "timeout": 0, "tokens": 0},
    2: {"calls": 40, "time": 1.5, "effort": "medium", "timeout": 75, "tokens": 16000},
    3: {"calls": 70, "time": 2.0, "effort": "high", "timeout": 120, "tokens": 24000},
}
# Input tokens one job may consume in 24 hours before further runs wait. Failed jobs were
# retried until they had used millions of tokens (a long form costs ~450k per failed run).
JOB_TOKEN_CAP = 1_600_000
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


def time_budget(budget, tier, ats="", boost=1.0):
    # No Workday extension: confirmed Workday runs take 4-5 minutes; a longer budget only let
    # failing runs burn 20 minutes (owner, 2026-10-14). `boost` is a specialist's multiplier (pool_boost).
    return int(min(1200 * boost, budget * TIERS[clamp(tier)]["time"] * boost))
