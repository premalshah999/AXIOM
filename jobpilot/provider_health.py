"""Bound provider spending during outages; recovery uses real work, never probes."""

import asyncio
import hashlib
import time
from datetime import UTC, datetime
from uuid import uuid4

from .db import Setting

COOLDOWN_SECONDS = 300


def key(config):
    identity = f"{config.llm_provider}:{config.llm_base_url}:{config.llm_model}"
    return "provider_health:" + hashlib.sha256(identity.encode()).hexdigest()


def acquire(db, config, timeout):
    """Return generation/lease, or None when requests are temporarily withheld."""
    at = time.time()
    with db.exclusive() as s:
        row = s.get(Setting, key(config))
        value = dict(row.value) if row else {}
        generation = value.get("generation", 0)
        if not value.get("retry_at"):
            return generation, None
        if value["retry_at"] > at or value.get("lease_until", 0) > at:
            return None
        lease = uuid4().hex
        row.value = {**value, "lease": lease, "lease_until": at + timeout + 10}
        return generation, lease


def finish(db, config, ticket, *, failure=None, retry_after=None):
    """Late replies from a prior generation cannot clear an open cooldown."""
    if failure in {"timeout", "client_error"}:
        # A slow individual completion is not evidence of a shared outage.
        # Its owner defers; other owners keep their independent requests.
        # A rejected request (HTTP 400) concerns that payload, not the provider's availability.
        return
    with db.exclusive() as s:
        row = s.get(Setting, key(config))
        value = dict(row.value) if row else {}
        generation, lease = ticket
        if generation != value.get("generation", 0) or lease != value.get("lease"):
            return
        if failure is None:
            if row:
                row.value = {"generation": generation, "failures": 0}
            return
        failures = value.get("failures", 0) + 1
        value = {"generation": generation, "failures": failures, "last_error": failure}
        if failures >= 2 or lease or failure == "rate_limited":
            seconds = (
                (max(1, min(3600, retry_after)) if retry_after is not None else 60)
                if failure == "rate_limited"
                else COOLDOWN_SECONDS
            )
            value.update(generation=generation + 1, retry_at=time.time() + seconds)
        if row:
            row.value = value
        else:
            s.add(Setting(key=key(config), value=value))


async def wait_out_short_cooldown(db, config, limit=90):
    """Wait through a cooldown that ends within `limit` seconds instead of parking the run for it.

    Longer cooldowns return at once, so acquire() refuses them exactly as before.
    """
    retry = (db.get_setting(key(config), {}) or {}).get("retry_at")
    left = (retry - time.time()) if retry else 0
    if 0 < left <= limit:
        await asyncio.sleep(left + 0.1)


def release(db, config, ticket):
    """Owner cancellation releases the recovery lease without reporting an outage."""
    generation, lease = ticket
    if not lease:
        return
    with db.exclusive() as s:
        row = s.get(Setting, key(config))
        if row and row.value.get("generation", 0) == generation and row.value.get("lease") == lease:
            row.value = {k: v for k, v in row.value.items() if k not in {"lease", "lease_until"}}


def status(db, config):
    value = db.get_setting(key(config), {})
    at = time.time()
    retry = value.get("retry_at")
    state = "ready"
    if retry:
        state = "cooldown" if retry > at else "recovery_ready"
        if value.get("lease_until", 0) > at:
            state = "recovering"
    return {
        "provider": config.llm_provider,
        "model": config.llm_model,
        "state": state,
        "retry_at": datetime.fromtimestamp(retry, UTC).isoformat() if retry else None,
        "last_error": value.get("last_error"),
    }


def selected(db, config):
    from .agent_settings import current, for_role

    helpers = current(db, config).enabled
    return {
        role: status(db, for_role(db, config, role))
        for role in ("answers", "navigator")
        if getattr(helpers, role)
    }


def dispatch_limit(db, config):
    states = [s["state"] for s in selected(db, config).values()]
    if any(state in {"cooldown", "recovering"} for state in states):
        return 0
    # HTTP acquire() already allows exactly one productive recovery request.
    # A browser can spend time on local fields/mail before needing the model;
    # reducing the entire application pool to that browser stalls the others.
    return config.workers
