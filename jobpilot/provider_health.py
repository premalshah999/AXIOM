"""Bound provider spending during outages; recovery uses real work, never probes."""

import asyncio
import hashlib
import time
from datetime import UTC, datetime
from uuid import uuid4

from . import host_health
from .db import Setting

COOLDOWN_SECONDS = 300
# An account out of credit stays out until someone tops it up: each 5-minute recovery start was a real application that
# failed at its first model question and spent one of its attempts (2026-10-25).
QUOTA_COOLDOWN_SECONDS = 1800
# A connection that failed outright (no route, DNS, reset: a Mac waking from sleep or switching networks) says nothing
# about the provider. The five-minute cooldown it opened stopped runs that had made no call at all: 3 of 17 runs that
# ended "model provider is unavailable" on 2026-10-31 sent nothing, and one had seen a single refused connection. The
# window is short and open to everyone afterwards: no one-request-at-a-time recovery lease, which sent the runs that
# had waited through it to "unavailable" anyway.
TRANSPORT_COOLDOWN_SECONDS = 20


# Replies with HTTP 200 that are not readable completions (Sail Research's own service_tier, 2026-10-28) leave no mark on
# the cooldown: the transport counted every one a success, so parked runs retried every 1-2 minutes against a provider
# that answered each of them unreadably. This many in a row (a reply that reads cancels the count) open the cooldown.
INVALID_AFTER = 3
INVALID_WINDOW_SECONDS = 180
# A provider that just "recovered" on a probe and answers unreadably again is not recovered.
RECOVERY_GRACE_SECONDS = 90
INVALID = "invalid_response"
# Failures only the account's owner can end. They wait longer than an outage, say what to do, and a new key ends them at once.
ACCOUNT_FAILURES = {"quota_exhausted", "auth_rejected"}
ACCOUNT_REASONS = {
    "auth_rejected": "The AI provider refused your API key. Paste a working key in Settings > Connections; saved work is kept",
    "quota_exhausted": "Your AI provider account is out of credit. Top it up, or paste another key in Settings > Connections; "
    "saved work is kept",
}
_invalid: dict[str, list[float]] = {}


def key(config):
    identity = f"{config.llm_provider}:{config.llm_base_url}:{config.llm_model}"
    return "provider_health:" + hashlib.sha256(identity.encode()).hexdigest()


def key_digest(config):
    return hashlib.sha256(str(config.llm_api_key or "").encode()).hexdigest()[:16]


def replaced_key(value, config):
    """An account failure recorded against a key that is no longer the one in use says nothing about this one."""
    return value.get("last_error") in ACCOUNT_FAILURES and value.get("key") != key_digest(config)


def acquire(db, config, timeout):
    """Return generation/lease, or None when requests are temporarily withheld."""
    at = time.time()
    with db.exclusive() as s:
        row = s.get(Setting, key(config))
        value = dict(row.value) if row else {}
        generation = value.get("generation", 0)
        if value.get("retry_at") and replaced_key(value, config):
            row.value = {"generation": generation, "failures": 0}
            return generation, None
        if not value.get("retry_at"):
            return generation, None
        if value["retry_at"] > at:
            return None
        if value.get("last_error") == "transport_error":
            return generation, None
        if value.get("lease_until", 0) > at:
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
                # A success that ends a cooldown is the signal parked runs wait for (recovery.retry_due): 35 of 45
                # jobs parked on "model provider unavailable" finished later, but only after their own 1-2 minute timer.
                row.value = {"generation": generation, "failures": 0, **({"recovered_at": time.time()} if value.get("retry_at") else {k: v for k, v in value.items() if k == "recovered_at"})}
            return
        failures = value.get("failures", 0) + 1
        value = {"generation": generation, "failures": failures, "last_error": failure}
        if failure in ACCOUNT_FAILURES:
            value["key"] = key_digest(config)
        if failures >= 2 or lease or failure == "rate_limited" or failure in ACCOUNT_FAILURES:
            seconds = (
                (max(1, min(3600, retry_after)) if retry_after is not None else 60)
                if failure == "rate_limited"
                else QUOTA_COOLDOWN_SECONDS
                if failure in ACCOUNT_FAILURES
                else TRANSPORT_COOLDOWN_SECONDS
                if failure == "transport_error"
                else COOLDOWN_SECONDS
            )
            value.update(generation=generation + 1, retry_at=time.time() + seconds)
        if row:
            row.value = value
        else:
            s.add(Setting(key=key(config), value=value))


def note_invalid_response(db, config) -> bool:
    """Count an unreadable HTTP 200 reply; the third in a row inside three minutes (the first, straight after a probe
    reported recovery) opens the cooldown. True when the cooldown is open."""
    k, at = key(config), time.time()
    hits = [t for t in _invalid.get(k, []) if t > at - INVALID_WINDOW_SECONDS] + [at]
    _invalid[k] = hits
    with db.exclusive() as s:
        row = s.get(Setting, k)
        value = dict(row.value) if row else {}
        if value.get("retry_at", 0) > at:
            return True
        probed = at - float(value.get("recovered_at", 0) or 0) <= RECOVERY_GRACE_SECONDS
        if len(hits) < INVALID_AFTER and not probed:
            return False
        value = {
            "generation": value.get("generation", 0) + 1,
            "failures": len(hits),
            "last_error": INVALID,
            "retry_at": at + COOLDOWN_SECONDS,
        }
        if row:
            row.value = value
        else:
            s.add(Setting(key=k, value=value))
    _invalid.pop(k, None)
    return True


def note_readable_response(config):
    """A reply that read ends the run of unreadable ones."""
    if _invalid:
        _invalid.pop(key(config), None)


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
    if retry and replaced_key(value, config):
        retry = None
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


def account_reason(db, config):
    """The owner's fix, when the provider is held back by a refused key or an empty account rather than an outage.

    A run parked behind such a cooldown said only "The selected model provider is unavailable", the same words as a
    provider outage, so nothing told the owner (or a friend on a fresh install) that a key or a top-up was what was
    needed (18 runs 2026-10-31/11-01 carried it). A replaced key ends the cooldown (status), so only a live one reads."""
    for state in selected(db, config).values():
        if state["state"] in {"cooldown", "recovering"} and state.get("last_error") in ACCOUNT_REASONS:
            return ACCOUNT_REASONS[state["last_error"]]
    return None


def dispatch_gate(db, config):
    """Whether, and how widely, new work may start: the model's cooldown first, then the host (host_health.check)."""
    chosen = selected(db, config).values()
    if any(s["state"] in {"cooldown", "recovering"} for s in chosen):
        stuck = [s.get("last_error") for s in chosen if s["state"] in {"cooldown", "recovering"}]
        unreadable = INVALID in stuck
        account = next((ACCOUNT_REASONS[e] for e in stuck if e in ACCOUNT_REASONS), None)
        return host_health.Gate(
            0,
            "provider_unavailable",
            account
            or (
                "The selected model keeps answering with replies that cannot be read (invalid response); saved work is retained"
                if unreadable
                else "The selected model is cooling down after failed requests; saved work is retained"
            ),
        )
    # HTTP acquire() already allows exactly one productive recovery request.
    # A browser can spend time on local fields/mail before needing the model;
    # reducing the entire application pool to that browser stalls the others.
    # The host can still be too slow or the worker Chrome gone: then fewer applications run at once, or none.
    return host_health.check(config.workers, db=db)


def dispatch_limit(db, config):
    return dispatch_gate(db, config).limit


def recovered_since(db, config, iso):
    """True when a request succeeded after a cooldown, later than the ISO time `iso`."""
    value = db.get_setting(key(config), {}) or {}
    try:
        return float(value.get("recovered_at", 0)) > datetime.fromisoformat(iso).timestamp()
    except (TypeError, ValueError):
        return False
