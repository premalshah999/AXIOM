"""Durable, bounded recovery of approved drafts. Final submits are never replayed."""

from datetime import UTC, datetime, timedelta
import hashlib
import json
import re

from sqlalchemy import select

from .application_budget import BROWSER_OUTAGE, HOST_OVERLOADED, nothing_sent
from .db import Event, Job, Review, Run, Setting, now
from .admission import employer_partition, outcome_for
from .specialist import handoff_threshold, handoff_trigger

CAUSES = {
    "site_temporary",
    "control_timeout",
    "operation_timeout",
    "navigation_stalled",
    "worker_interrupted",
    "control_observation",
    "validation_controls",
    "model_output",
    "email_matched",
    "sender_busy",
    "browser_unavailable",
    # One model timeout parked Lumen and PayPal (2026-10-14) while 293/295 calls succeeded.
    "provider_unavailable",
    # A deadline timeout while the host's event loop ran late (host_health): nothing was sent.
    "host_overloaded",
}
SENDER_BUSY = "Another application kept the same email sender busy"
# Lease waits more than an hour apart are separate attempts: Ford's two waits on 10-17 exhausted its 10-18 retry.
SENDER_CHAIN_SECONDS = 3600
# Waits that sent nothing: (first delay, longest delay, waits before giving up; None = never). Each wait doubles the
# delay. A busy sender used to give up after two 15-30 s waits and land in Repairing with "Requeue this job" although
# the lease frees by itself (retry_due starts nothing while it is held); a model outage requeued every 1-2 minutes
# for as long as it lasted, one job eight times in 12 minutes (2026-10-28, 102 runs on 50 jobs).
WAIT_BACKOFF = {
    "sender_busy": (15, 300, 40),
    "browser_unavailable": (30, 600, 20),
    "host_overloaded": (30, 600, 20),
    "provider_unavailable": (60, 1800, None),
}


def backoff_seconds(waits, first, longest):
    """Delay before the next try after `waits` consecutive waits: first, 2x, 4x ... up to longest."""
    return min(longest, first * 2 ** max(0, waits - 1))


def nothing_sent_streak(rows):
    """Trailing runs (oldest first, (state, reason)) that stopped on the app's own infrastructure, newest backwards."""
    streak = 0
    for state, reason in reversed(list(rows)):
        if not nothing_sent(state, reason):
            break
        streak += 1
    return streak


# A deterministic stall (a control or an answer the engine could not get past) repeats exactly while the code is the same.
# A sign-in form that came back unchanged is one (accounts/flow_handle.py): Allen Institute's ClearCompany sign-in came back
# unchanged on run after run (2026-10-30) because the employer signs in by an emailed link the password form never uses.
STALLED = re.compile(
    r"^(?:Recovery stopped:|The browser did not advance|The employer returned its sign-in page without accepting)"
)


def repeats_unchanged(rows, repeats=2):
    """True when the last `repeats` runs (oldest first, (reason, engine_version)) stalled for the same reason on the same
    engine: another try runs the same failing step. The Required-field and Country stalls repeated 9-14 times over
    three days (2026-10-26..28) because the autopilot's daily retry ignored the recovery signature."""
    tail = [tuple(row) for row in list(rows)[-repeats:]]
    return (
        len(tail) == repeats
        and bool(tail[0][1])
        and all(STALLED.match(reason or "") for reason, _ in tail)
        and len(set(tail)) == 1
    )
REPAIR_STRATEGY_VERSION = "owned-review-repair-observation-v4"
# Browser/control repairs have only eight minutes including all waits. Provider
# and site outages retain their own admission/circuit gates.
LOCAL_RETRY_DELAY_SECONDS = 30
SPECIALIST_CAUSES = {
    "control_timeout",
    "operation_timeout",
    "navigation_stalled",
    "control_observation",
    "validation_controls",
    "model_output",
}
SPECIALIST_BLOCKERS = {"control_failure", "observation_failure", "validation", "model_output"}
SAFE_FAILURE_STATES = {
    "stale",
    "covered",
    "disabled",
    "missing",
    "timeout",
    "invalid",
    "unsupported",
    "no_progress",
    "invalid_model_output",
    "failed",
    "rejected",
    "unchanged",
    "refused",
}
SAFE_OPERATIONS = {
    "retrieve",
    "fill",
    "click",
    "inspect_options",
    "choose",
    "search",
    "back_menu",
    "scroll",
    "date",
    "wait",
    "help",
    "back",
    "return_to_job",
    "dismiss",
}


def automatic_attempts(session, job_id):
    """Count persisted retry runs so a release cannot erase an exhausted budget."""
    return sum(
        bool(
            run.packet.get("source_run_id")
            and run.packet.get("worker_pool", "normal") != "specialist"
            and not run.packet.get("email_wait_resume")
            and run.packet.get("recovery_continuation_cause") != "worker_interrupted"
            # Waiting behind a sibling's email, a dead Chrome or an overloaded host sent nothing; not a repair attempt.
            and not (run.reason or "").startswith((SENDER_BUSY, *BROWSER_OUTAGE, HOST_OVERLOADED))
        )
        for run in session.scalars(select(Run).where(Run.job_id == job_id))
    )


def specialist_attempts(session, job_id):
    """A committed specialist run consumes capacity even before its browser starts."""
    return sum(
        run.packet.get("worker_pool") == "specialist"
        and run.mode == "submit"
        and not run.packet.get("verification_only")
        and not run.packet.get("email_wait_resume")
        and run.packet.get("recovery_continuation_cause") != "worker_interrupted"
        for run in session.scalars(select(Run).where(Run.job_id == job_id))
    )


def _safe_strategy_summary(checkpoint):
    """Only opaque strategy IDs, counts and fixed failure categories enter handoffs."""
    output = []
    strategies = checkpoint.get("failed_strategies", {})
    if not isinstance(strategies, dict):
        return output
    for key, strategy in list(strategies.items())[:64]:
        if key == "__navigation_outcomes__" and isinstance(strategy, list):
            for attempt in strategy[-12:]:
                if (
                    not isinstance(attempt, dict)
                    or not isinstance(attempt.get("action"), str)
                    or attempt.get("action") not in SAFE_OPERATIONS
                ):
                    continue
                outcome = attempt
                if isinstance(attempt.get("outcome"), str):
                    try:
                        parsed = json.loads(attempt["outcome"])
                        outcome = parsed if isinstance(parsed, dict) else {}
                    except ValueError:
                        outcome = {}
                category = outcome.get("failure_category")
                output.append(
                    {
                        "operation": attempt["action"],
                        **(
                            {"failure_category": category}
                            if isinstance(category, str) and category in SAFE_FAILURE_STATES
                            else {}
                        ),
                        **{
                            k: outcome[k]
                            for k in ("executed", "verified", "ok")
                            if isinstance(outcome.get(k), bool)
                        },
                    }
                )
            continue
        if not isinstance(strategy, dict) or not isinstance(strategy.get("attempts"), list):
            continue
        attempts = strategy["attempts"]
        states = [
            attempt.get("state")
            for attempt in attempts
            if isinstance(attempt, dict)
            and isinstance(attempt.get("state"), str)
            and attempt.get("state") in SAFE_FAILURE_STATES
        ]
        output.append(
            {
                "strategy_id": hashlib.sha256(str(key).encode()).hexdigest()[:16],
                "attempt_count": len(attempts),
                "failure_states": states[-6:],
            }
        )
    return output


def _submission_receipt(receipt):
    if not isinstance(receipt, dict) or not receipt:
        return False
    email = receipt.get("email")
    return bool(
        receipt.get("type")
        in {
            "explicit_confirmation_page",
            "application_created",
            "explicit_rejection_page",
            "explicit_validation_rejection",
            "user_reconciled",
            "final_page_confirmation",
            "authenticated_email",
            "already_applied",
        }
        or receipt.get("website_confirmed")
        or receipt.get("email_confirmed")
        or isinstance(email, dict)
        and email.get("authenticated")
    )


def _eligible_specialist_history(session, run, result):
    """Handoff follows a stopped writer and requires two evidenced normal failures."""
    from .service import Service

    job = session.get(Job, run.job_id)
    control = session.get(Setting, "control")
    job_control = session.get(Setting, "job_control:" + run.job_id)
    desktop = session.get(Setting, "desktop_job:" + run.job_id)
    if (
        not job
        or job.demo
        or job.status
        in {
            "archived",
            "cancelled",
            "paused",
            "confirmed",
            "submission_unknown",
            "already_applied",
            "submitting",
        }
        or control
        and control.value.get("paused")
        or job_control
        and job_control.value.get("state") in {"paused", "deleted"}
        or desktop
        and desktop.value.get("armed")
        or result.get("waiting_kind") == "email"
        or any(Service.review_kind(q) not in SPECIALIST_BLOCKERS for q in result.get("reviews", []))
        or _submission_receipt(result.get("receipt"))
        or result.get("recovery_cause") not in SPECIALIST_CAUSES
        or result.get("blocker_type") not in SPECIALIST_BLOCKERS
    ):
        return []
    history = list(
        session.scalars(select(Run).where(Run.job_id == run.job_id).order_by(Run.created_at, Run.id))
    )
    for other in history:
        if (
            other.state
            in {"queued", "running", "submitting", "confirmed", "submission_unknown", "already_applied"}
            or _submission_receipt(other.receipt)
            or other.packet.get("reservation_run_id")
            or other.packet.get("verification_only")
        ):
            return []
    # The reservation's durable event survives a later explicit rejection state.
    ids = [other.id for other in history]
    if ids and session.scalar(
        select(Event.id).where(Event.run_id.in_(ids), Event.kind == "submitting").limit(1)
    ):
        return []
    if ids and any(
        Service.review_kind({"key": review.key, "reason": review.reason}) not in SPECIALIST_BLOCKERS
        for review in session.scalars(select(Review).where(Review.run_id.in_(ids), Review.answer.is_(None)))
    ):
        return []
    eligible = []
    for other in history:
        checkpoint = (result.get("browser_checkpoint") if other.id == run.id else None) or other.packet.get(
            "browser_checkpoint"
        )
        cause = result.get("recovery_cause") if other.id == run.id else other.packet.get("recovery_cause")
        blocker = result.get("blocker_type") if other.id == run.id else other.packet.get("blocker_type")
        if (
            other.packet.get("worker_pool", "normal") == "normal"
            and other.state in {"waiting_browser", "timed_out", "failed"}
            and cause in SPECIALIST_CAUSES
            and blocker in SPECIALIST_BLOCKERS
            and isinstance(checkpoint, dict)
            and checkpoint.get("resume_sha")
            and checkpoint.get("failed_strategies")
            and other.packet.get("waiting_kind") != "email"
            and not other.packet.get("email_wait_resume")
            and other.mode == "submit"
            and other.packet.get("match_approved")
        ):
            eligible.append(other)
    return eligible


def failure_signature(run, result):
    checkpoint = result.get("browser_checkpoint") or run.packet.get("browser_checkpoint", {})
    strategies = checkpoint.get("failed_strategies", {})
    reason = re.sub(r"\s+", " ", result.get("reason", "").casefold()).strip()
    # "Recovery stopped: <the field or answer that blocks>" already names the blocker; the last state of each strategy
    # then only varies between attempts (a different refused click) and hid a repeated stall from the unchanged-failure
    # check: one job stopped 14 times on the same blocker (2026-10-26..28).
    states = [] if reason.startswith("recovery stopped:") else sorted(
        (key, attempts[-1].get("state", ""))
        for key, value in strategies.items()
        if isinstance(value, dict) and (attempts := value.get("attempts"))
    )
    return hashlib.sha256(
        json.dumps(
            {
                "cause": result.get("recovery_cause"),
                "blocker": result.get("blocker_type"),
                "reason": reason,
                "states": states,
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()


def schedule_version_repairs(service):
    """One new bounded recovery budget when the execution strategy has changed.

    Missing facts, owner controls and any uncertain/confirmed submission remain
    protected. A release cannot repeatedly reset a draft's budget on startup.
    """
    from .runtime import engine_version

    version = engine_version()
    scheduled = []
    with service.db.exclusive() as s:
        latest, protected = {}, set()
        # Metadata only: every run's saved packet (6,498 runs, 841 MB stored) grew startup past the 7.6 GB Docker VM
        # and the server was OOM-killed every 40 s (2026-10-24). Only the few candidate drafts' packets are read.
        for row in s.execute(select(Run.id, Run.job_id, Run.state, Run.mode).order_by(Run.created_at.desc())):
            latest.setdefault(row.job_id, row)
            if row.state in {"confirmed", "submission_unknown", "already_applied", "submitting"}:
                protected.add(row.job_id)
        for job_id, row in latest.items():
            if job_id in protected or row.state not in {"waiting_browser", "timed_out"} or row.mode != "submit":
                continue
            run = s.get(Run, row.id)
            job = s.get(Job, job_id)
            control = s.get(Setting, "job_control:" + job_id)
            if (
                job_id in protected
                or not job
                or job.status == "archived"
                or control
                and control.value.get("state") in {"paused", "deleted"}
                or run.state not in {"waiting_browser", "timed_out"}
                or run.mode != "submit"
                or not run.packet.get("match_approved")
                or not run.packet.get("profile", {}).get("autonomous")
                or not run.packet.get("engine_version")
                or run.packet["engine_version"] == version
                or run.packet.get("worker_pool") == "specialist"
                or run.packet.get("specialist_escalation", {}).get("eligible")
                or not (
                    run.packet.get("blocker_type")
                    in {"model_output", "control_failure", "observation_failure", "validation"}
                    or run.packet.get("recovery_cause") in CAUSES
                )
            ):
                continue
            key = "draft_recovery:" + job_id
            recovery = s.get(Setting, key)
            if recovery and recovery.value.get("repair_strategy_version") == REPAIR_STRATEGY_VERSION:
                continue
            attempts = max(
                (recovery.value.get("attempts", 0) if recovery else 0), automatic_attempts(s, job_id)
            )
            # A changed repair strategy gets at most one extra lifetime attempt.
            # Engine hashes also change for unrelated features: they are not a budget.
            limit = service.config.recovery_max_attempts + 1
            value = {
                "source_run_id": run.id,
                "cause": "control_observation",
                "attempts": attempts,
                "attempt_limit": limit,
                "scheduled": bool(service.config.recovery_max_attempts) and attempts < limit,
                "status": "changed_strategy" if attempts < limit else "exhausted",
                "not_before": now(),
                "updated_at": now(),
                "repair_engine_version": version,
                "repair_strategy_version": REPAIR_STRATEGY_VERSION,
                "prior_engine_version": run.packet["engine_version"],
            }
            if recovery:
                recovery.value = {**recovery.value, **value}
            else:
                s.add(Setting(key=key, value=value))
            scheduled.append(job_id)
    return scheduled


def schedule_recovery(session, config, run, result):
    from .application_budget import require_allowance
    from .admission import QueueBlocked

    if run.mode == "submit":
        try:
            require_allowance(session, run.job_id)
        except QueueBlocked as exc:
            row = session.get(Setting, "draft_recovery:" + run.job_id)
            value = {"scheduled": False, "status": exc.code, "source_run_id": run.id, "updated_at": now()}
            if row:
                row.value = {**row.value, **value}
            else:
                session.add(Setting(key="draft_recovery:" + run.job_id, value=value))
            return
    cause = result.get("recovery_cause")
    if (
        cause not in CAUSES
        or run.state not in {"waiting_browser", "timed_out"}
        or run.mode != "submit"
        or not run.packet.get("match_approved")
        or not run.packet.get("profile", {}).get("autonomous")
        or not config.recovery_max_attempts
    ):
        return
    key = "draft_recovery:" + run.job_id
    row = session.get(Setting, key)
    previous = row.value if row else {}
    if cause == "worker_interrupted":
        # Deployment interrupted an authorized browser owner before submission.
        # Resume its saved work immediately without consuming another repair.
        value = {
            **previous, "source_run_id": run.id, "cause": cause,
            "scheduled": True, "status": "scheduled", "not_before": now(), "updated_at": now(),
        }
        if row:
            row.value = value
        else:
            session.add(Setting(key=key, value=value))
        return
    if cause in {"sender_busy", "browser_unavailable", "provider_unavailable", "host_overloaded"}:
        # Another owner holds this employer's email sender (usually a sibling job
        # waiting for its verification email), the browser or model is down, or the host is overloaded.
        # Nothing was sent: defer behind it without spending a repair attempt, marking an unchanged
        # failure or opening the employer circuit. retry_due also waits for host_health's gate.
        chained = previous.get("updated_at", "") >= (
            datetime.now(UTC) - timedelta(seconds=SENDER_CHAIN_SECONDS)
        ).isoformat()
        waits = (previous.get("sender_waits", 0) if chained else 0) + 1
        first, longest, limit = WAIT_BACKOFF[cause]
        keep = limit is None or waits <= limit
        value = {
            **previous,
            "source_run_id": run.id,
            "cause": cause,
            "sender_waits": waits,
            "mail_request_kind": result.get("mail_request_kind", previous.get("mail_request_kind", "auto")),
            "scheduled": keep,
            "status": cause if keep else cause + "_exhausted",
            "not_before": (datetime.now(UTC) + timedelta(seconds=backoff_seconds(waits, first, longest))).isoformat(),
            "updated_at": now(),
        }
        if row:
            row.value = value
        else:
            session.add(Setting(key=key, value=value))
        return
    attempts = max(previous.get("attempts", 0), automatic_attempts(session, run.job_id))
    limit = previous.get("attempt_limit", config.recovery_max_attempts)
    signature = failure_signature(run, result)
    unchanged = (
        cause not in {"worker_interrupted", "email_matched"}
        and previous.get("failure_signature") == signature
        and previous.get("failure_engine_version") == run.packet.get("engine_version")
        and previous.get("source_run_id") != run.id
    )
    exhausted = attempts >= limit
    from .pipeline_comparison import SPECIALIST, pool_for

    hard_attempts = specialist_attempts(session, run.job_id)
    hard_limit = config.specialist_attempts
    eligible = _eligible_specialist_history(session, run, result)
    ats = (session.get(Job, run.job_id) or Job()).ats or ""
    needed = handoff_threshold(config, ats, cause)
    handoff = pool_for(run) == SPECIALIST or (needed is not None and len(eligible) >= needed)
    if handoff and eligible:
        checkpoint = result.get("browser_checkpoint") or run.packet.get("browser_checkpoint", {})
        envelope = {
            "eligible": True,
            "original_run_id": eligible[0].id,
            "source_run_id": run.id,
            "normal_failure_run_ids": [other.id for other in eligible],
            "cause": cause,
            "failure_signature": signature,
            "safe_failed_strategies": _safe_strategy_summary(checkpoint),
            "attempt_limit": hard_limit,
            "trigger": handoff_trigger(config, ats, cause, len(eligible)) or "specialist_retry",
        }
        from .pipeline_comparison import normalize_ats

        envelope["context"] = {
            "ats_specialist": normalize_ats(session.get(Job, run.job_id).ats),
            "cause": cause,
            "normal_failure_run_ids": envelope["normal_failure_run_ids"],
            "safe_failed_strategies": envelope["safe_failed_strategies"],
        }
        run.packet = {**run.packet, "specialist_escalation": envelope}
        value = {
            **previous,
            "source_run_id": run.id,
            "cause": cause,
            "attempts": attempts,
            "normal_attempts": attempts,
            "attempt_limit": limit,
            "specialist_attempts": hard_attempts,
            "specialist_attempt_limit": hard_limit,
            "worker_pool": SPECIALIST,
            "failure_signature": signature,
            "failure_engine_version": run.packet.get("engine_version"),
            "scheduled": hard_attempts < hard_limit,
            "status": "specialist_scheduled" if hard_attempts < hard_limit else "specialist_exhausted",
            "not_before": now(),
            "updated_at": now(),
        }
        if row:
            row.value = value
        else:
            session.add(Setting(key=key, value=value))
        return
    if pool_for(run) == SPECIALIST:
        # Losing eligibility cannot silently send a difficult draft back to normal
        # retries or reset its independent attempt limit.
        value = {
            **previous,
            "source_run_id": run.id,
            "cause": cause,
            "scheduled": False,
            "status": "specialist_blocked",
            "specialist_attempts": hard_attempts,
            "specialist_attempt_limit": hard_limit,
            "updated_at": now(),
        }
        if row:
            row.value = value
        else:
            session.add(Setting(key=key, value=value))
        return
    value = {
        **{
            key: previous[key]
            for key in ("repair_engine_version", "repair_strategy_version")
            if key in previous
        },
        "source_run_id": run.id,
        "cause": cause,
        "attempts": attempts,
        "attempt_limit": limit,
        "failure_signature": signature,
        "failure_engine_version": run.packet.get("engine_version"),
        "scheduled": not exhausted and not unchanged,
        "status": "exhausted" if exhausted else "unchanged_failure" if unchanged else "scheduled",
        # What stopped it, in the stop's own words, for the status page and the owner (not the generic "did not advance").
        **({"blocker": (result.get("reason") or "")[:300]} if exhausted or unchanged else {}),
        "not_before": (
            datetime.now(UTC) + timedelta(seconds=(
                config.recovery_delay_seconds if cause == "site_temporary"
                else min(config.recovery_delay_seconds, LOCAL_RETRY_DELAY_SECONDS)
            ) * (attempts + 1))
        ).isoformat(),
        "updated_at": now(),
    }
    # The site named its own wait (Oracle: "Try again in 30 minutes"); trying sooner only extends it.
    cooldown = int(result.get("site_cooldown_seconds") or 0) if cause == "site_temporary" else 0
    if cooldown:
        value["not_before"] = (datetime.now(UTC) + timedelta(seconds=cooldown)).isoformat()
    if row:
        row.value = value
    else:
        session.add(Setting(key=key, value=value))
    if cooldown or (unchanged and cause == "site_temporary"):
        # Only an observed recurring site failure blocks this exact employer.
        # Widget/model failures are job-local and cannot disable an ATS family.
        job = session.get(Job, run.job_id)
        partition = employer_partition(job)
        key = "site_circuit:" + hashlib.sha256(partition.encode()).hexdigest()
        circuit = session.get(Setting, key)
        until = (datetime.now(UTC) + timedelta(seconds=cooldown or 600)).isoformat()
        data = {"until": until, "source_run_id": run.id, "failure_signature": signature}
        if circuit:
            circuit.value = data
        else:
            session.add(Setting(key=key, value=data))


# Blockers that belong to an employer's site rather than one job: its CAPTCHA refuses this browser's answers, or its
# account for the applicant's email cannot be used.
SITE_BLOCKERS = {"captcha", "account"}
SITE_BLOCKER_JOBS, SITE_BLOCKER_HOURS = 3, 6


STUCK_FIELD = re.compile(r"^(?:Required field incomplete|Needs an approved answer|Value not accepted): (.{1,200})$")


def stuck_field(session, run_id):
    """The field a run's last failed read-back named first, or ''."""
    for data in session.scalars(
        select(Event.data).where(Event.run_id == run_id, Event.kind == "verification").order_by(Event.created_at.desc()).limit(5)
    ):
        for problem in (data or {}).get("problems") or []:
            if match := STUCK_FIELD.match(str(problem)):
                return match[1].strip()
    return ""


def open_blocked_site_circuit(session, run, state):
    """Three different jobs on one employer site stopped by the same site blocker, or stalled on the same required
    field, within six hours: that site's other jobs wait six hours and the workers go to other employers. JPMorgan's
    Oracle hCaptcha and L3Harris' unusable account took most of 2026-10-24's Oracle and SuccessFactors starts; Emory's
    unreadable education menu held iCIMS's single slot through eight retries."""
    if state != "waiting_browser":
        return False
    blocker = (run.packet or {}).get("blocker_type")
    field = "" if blocker in SITE_BLOCKERS else stuck_field(session, run.id)
    if blocker not in SITE_BLOCKERS and not field:
        return False
    job = session.get(Job, run.job_id)
    if not job:
        return False
    partition = employer_partition(job)
    since = (datetime.now(UTC) - timedelta(hours=SITE_BLOCKER_HOURS)).isoformat()
    rows = session.execute(
        select(Run.id, Run.job_id, Job.url, Job.ats, Run.packet["blocker_type"].as_string())
        .join(Job, Job.id == Run.job_id)
        .where(Run.state == "waiting_browser", Run.finished_at >= since, Job.ats == job.ats)
    ).all()
    same_site = [r for r in rows if employer_partition({"url": r[2], "ats": r[3]}) == partition]
    if field:
        jobs = {job_id for run_id, job_id, *_ in same_site if stuck_field(session, run_id) == field}
        blocker = f"'{field[:80]}' field"
    else:
        jobs = {job_id for _, job_id, _, _, kind in same_site if kind == blocker}
        blocker += " step"
    if len(jobs) < SITE_BLOCKER_JOBS:
        return False
    from .runtime import engine_version

    key = "site_circuit:" + hashlib.sha256(partition.encode()).hexdigest()
    data = {
        "until": (datetime.now(UTC) + timedelta(hours=SITE_BLOCKER_HOURS)).isoformat(),
        "source_run_id": run.id,
        "reason": f"{len(jobs)} applications on this employer's site stopped at the same {blocker}; its other jobs "
        "wait while other employers continue",
        "engine": engine_version(),
    }
    circuit = session.get(Setting, key)
    if circuit:
        circuit.value = data
    else:
        session.add(Setting(key=key, value=data))
    return True


async def retry_due(service):
    hold = service.db.get_setting("execution_v2_deployment_hold", {})
    if hold.get("stage") == "draining":
        return []
    canary_ids = (
        {j["id"] for j in service.db.get_setting("execution_v2_canary", {}).get("jobs", [])}
        if hold.get("stage") == "canary"
        else None
    )
    control = service.db.get_setting("control", {})
    if control.get("paused") or not control.get("auto_submit") or not service.dispatch:
        return []
    from . import provider_health
    from .provider_health import dispatch_limit

    provider_room = dispatch_limit(service.db, service.config)
    if not provider_room:
        return []
    with service.db.session() as s:
        from .pipeline_comparison import POOLS, SPECIALIST, pool_capacity

        pools = {pool: pool_capacity(s, pool, service.config.topology) for pool in POOLS}
        occupied = sum(pool.occupied for pool in pools.values())
        room = max(0, provider_room - occupied)
        employers = set().union(*(pool.employers for pool in pools.values()))
        due = [
            (r.key.removeprefix("draft_recovery:"), dict(r.value))
            for r in s.scalars(select(Setting).where(Setting.key.startswith("draft_recovery:", autoescape=True)))
            if r.value.get("scheduled")
        ]
    # A parked model outage does not wait out its timer once the provider is back (provider_health.recovered_since):
    # every one resumes after a success; while only the cooldown has ended, one run probes it with real work.
    cooled = provider_health.status(service.db, service.config)["state"] == "recovery_ready"
    ripe = []
    for job_id, value in due:
        if value.get("not_before", "") <= now():
            ripe.append((job_id, value))
        elif value.get("cause") == "provider_unavailable":
            if provider_health.recovered_since(service.db, service.config, value.get("updated_at", "")):
                ripe.append((job_id, {**value, "early": "recovered"}))
            elif cooled:
                ripe.append((job_id, {**value, "early": "probe"}))
    due, probes = ripe, 0
    queued = []
    for job_id, recovery in sorted(due, key=lambda r: r[1]["not_before"]):
        if canary_ids is not None and job_id not in canary_ids:
            continue
        if len(queued) >= room:
            break
        if recovery.get("early") == "probe" and probes:
            continue
        if service.db.get_setting("control", {}).get("paused"):
            break
        try:
            with service.db.session() as s:
                job = s.get(Job, job_id)
                if job is None:
                    from .admission import QueueBlocked

                    raise QueueBlocked("Recovery job no longer exists", code="not_found")
                if job.ats in set(service.db.get_setting("ats_focus", {}).get("exclude", [])):
                    continue  # Owner focus: this platform is set aside for now.
                from .pipeline_comparison import assign_pool, pool_capacity

                source = s.get(Run, recovery["source_run_id"])
                from .application_budget import require_allowance

                require_allowance(s, job_id)
                if not service.mail.recovery_sender_ready(s, job, recovery):
                    continue  # No browser start or retry spent while the known lease is held.
                pool = assign_pool(s, job, source)
                if not pool_capacity(s, pool, service.config.topology).available:
                    continue  # A full pool must not stop due drafts in the other pool.
                if (pool == SPECIALIST and recovery.get("cause") != "worker_interrupted"
                        and specialist_attempts(s, job_id) >= service.config.specialist_attempts):
                    continue
                employer = employer_partition(job)
            if employer in employers:
                continue
            run = await service.queue(
                job_id, "submit", retry=True, recovery_source_run=recovery["source_run_id"]
            )
            queued.append(run["id"])
            probes += recovery.get("early") == "probe"
            employers.add(employer)
            service.db.event(
                run["id"],
                "automatic_recovery",
                "Resuming an approved draft after a temporary failure",
                {
                    "source_run_id": recovery["source_run_id"],
                    "cause": recovery["cause"],
                },
            )
        except ValueError as exc:
            # Normal approval, duplicate, cooldown and budget gates still apply.
            # Do not consume a retry while the owner has paused the app.
            if service.db.get_setting("control", {}).get("paused"):
                break
            with service.db.exclusive() as s:
                row = s.get(Setting, "draft_recovery:" + job_id)
                if row and row.value.get("source_run_id") == recovery["source_run_id"]:
                    outcome = outcome_for(exc)
                    row.value = {
                        **row.value,
                        "scheduled": outcome["outcome"] == "deferred",
                        "status": "deferred"
                        if outcome["outcome"] == "deferred"
                        else "blocked_by_queue_guard",
                        **({"not_before": outcome["not_before"]} if outcome["not_before"] else {}),
                        "queue_outcome": outcome,
                        "updated_at": now(),
                    }
        except Exception:
            # queue() committed its outbox before dispatch. Startup will deliver
            # it; never create another run for a failed delivery.
            continue
    return queued
