"""When an application moves from an ATS worker to a specialist, and how much more budget the specialist gets.

ATS workers (slots 1..NORMAL_WORKERS) take every new application and run the flow of its platform. Specialists
(the next SPECIALIST_WORKERS slots) take the difficult or already-failed ones. A job is handed over when one of these
triggers fires, and only after an agent-fixable failure: recovery.SPECIALIST_CAUSES and SPECIALIST_BLOCKERS still decide
that. Missing personal facts, email waits, provider outages and uncertain submissions are never retried speculatively.

  repeated_failure  SPECIALIST_AFTER_FAILURES (default 2) failures on ATS workers
  hard_ats          the job's platform is in SPECIALIST_HARD_ATS: one failure is enough
  stalled           the controller stalled on an unchanged form (SPECIALIST_ON_STALL): one failure is enough

The specialist then gets SPECIALIST_BUDGET_MULTIPLIER (default 1.5) times the wall-clock time, model calls and per-job
token cap of an ATS worker, and at most SPECIALIST_ATTEMPTS attempts.
"""

from .pipeline_comparison import SPECIALIST, normalize_ats

STALLED = "navigation_stalled"


def hard_ats(config):
    """The normalized platform ids that skip the second ATS-worker failure."""
    return frozenset(normalize_ats(item) for item in config.specialist_hard_ats.split(",") if item.strip())


def handoff_threshold(config, ats, cause):
    """ATS-worker failures after which this job goes to a specialist, or None when there are no specialists."""
    if config.specialist_workers < 1:
        return None
    if normalize_ats(ats) in hard_ats(config) or (config.specialist_on_stall and cause == STALLED):
        return 1
    return config.specialist_after_failures


def handoff_trigger(config, ats, cause, failures):
    """Why the job qualifies after `failures` ATS-worker failures, or None. Recorded on the run for diagnosis."""
    needed = handoff_threshold(config, ats, cause)
    if needed is None or failures < needed:
        return None
    if normalize_ats(ats) in hard_ats(config):
        return "hard_ats"
    if config.specialist_on_stall and cause == STALLED:
        return "stalled"
    return "repeated_failure"


def routing_note(assigned, ran_in, source_packet):
    """What a run's queued event says about its worker pool, so every handoff (or a handoff that did not happen)
    can be explained from the run's timeline: the trigger, the failures before it, and a busy specialist pool."""
    note = {"pool": ran_in}
    if assigned == SPECIALIST:
        escalation = (source_packet or {}).get("specialist_escalation") or {}
        note["specialist_trigger"] = escalation.get("trigger") or "unrecorded"
        note["after_failures"] = len(escalation.get("normal_failure_run_ids") or [])
        note["failure_cause"] = escalation.get("cause")
        if ran_in != SPECIALIST:
            note["specialist_fallback"] = (
                "The specialist workers were busy, so this run used an ATS worker with the regular budget"
            )
    return note


def pool_boost(config, pool):
    """Budget multiplier for a worker pool: specialists get more, ATS workers exactly what is configured."""
    return config.specialist_budget_multiplier if pool == SPECIALIST else 1.0
