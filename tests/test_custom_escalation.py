"""A custom career site escalates its recovery strategy in its own lane (CODEX_CUSTOM_ATS_AUDIT item 3).

Every fixture takes its worker lane from the real router (assign_pool), never a hand-set pool: the old tests seeded a
normal-pool history for a custom job, a state the router cannot produce.
"""

from jobpilot.db import Job, Run
from jobpilot.pipeline_comparison import (
    CUSTOM,
    CUSTOM_ESCALATED,
    SPECIALIST,
    STRATEGY_ESCALATED,
    STRATEGY_STANDARD,
    assign_pool,
    assign_strategy,
    budget_class,
    escalated,
    routed_budget,
)
from jobpilot.recovery import automatic_attempts, schedule_recovery, specialist_attempts
from jobpilot.specialist import handoff_threshold, pool_boost, routing_note
from jobpilot.tiers import call_limit

STUCK = "control_observation"


def add_job(service, job_id, ats):
    with service.db.session() as s:
        s.add(
            Job(
                id=job_id,
                identity=job_id,
                url=f"https://{job_id}.example.test/careers/1",
                ats=ats,
                status="waiting_browser",
                company=job_id,
                title="Engineer",
            )
        )


def failed_run(service, job_id, run_id, ats="custom", *, source=None, cause=STUCK):
    """A stopped run whose lane and strategy come from the same routing the queue uses."""
    if not _exists(service, job_id):
        add_job(service, job_id, ats)
    with service.db.session() as s:
        job = s.get(Job, job_id)
        previous = s.get(Run, source) if source else None
        packet = {
            "match_approved": True,
            "profile": {"autonomous": True},
            "worker_pool": assign_pool(s, job, previous),
            "recovery_cause": cause,
            "blocker_type": "observation_failure" if cause == "navigation_stalled" else "control_failure",
            "browser_checkpoint": {
                "resume_sha": "abc",
                "failed_strategies": {"fill:email": {"attempts": [{"state": "failed"}]}},
            },
        }
        if assign_strategy(job, previous) == STRATEGY_ESCALATED:
            packet["strategy"] = STRATEGY_ESCALATED
        if previous and previous.packet.get("specialist_escalation"):
            packet["specialist_escalation"] = previous.packet["specialist_escalation"]
        s.add(
            Run(id=run_id, job_id=job_id, state="waiting_browser", mode="submit", reason="did not advance", packet=packet)
        )
        return packet


def _exists(service, job_id):
    with service.db.session() as s:
        return s.get(Job, job_id) is not None


def schedule(service, run_id, config=None, **result):
    with service.db.session() as s:
        run = s.get(Run, run_id)
        result = {
            "recovery_cause": run.packet["recovery_cause"],
            "blocker_type": run.packet["blocker_type"],
            "browser_checkpoint": run.packet["browser_checkpoint"],
            "reason": "Browser did not advance",
            **result,
        }
        schedule_recovery(s, config or service.config, run, result)
        escalation = run.packet.get("specialist_escalation")
    return service.db.get_setting("draft_recovery:" + run.job_id), escalation


def test_a_custom_site_runs_in_the_custom_lane_whatever_the_history(service):
    failed_run(service, "c0", "c0-1")
    with service.db.session() as s:
        assert assign_pool(s, s.get(Job, "c0"), s.get(Run, "c0-1")) == CUSTOM
        assert s.get(Run, "c0-1").packet["worker_pool"] == CUSTOM


def test_the_second_failure_of_a_custom_job_escalates_without_leaving_its_lane(service):
    failed_run(service, "c1", "c1-1")
    failed_run(service, "c1", "c1-2")
    recovery, escalation = schedule(service, "c1-2")
    assert recovery["status"] == "specialist_scheduled" and recovery["scheduled"] is True
    assert recovery["strategy"] == STRATEGY_ESCALATED and "worker_pool" not in recovery
    assert escalation["eligible"] is True and escalation["trigger"] == "repeated_failure"
    assert sorted(escalation["normal_failure_run_ids"]) == ["c1-1", "c1-2"]
    with service.db.session() as s:
        job, run = s.get(Job, "c1"), s.get(Run, "c1-2")
        assert assign_pool(s, job, run) == CUSTOM  # the lane is unchanged
        assert assign_strategy(job, run) == STRATEGY_ESCALATED
        assert routed_budget(s, job, "c1-2") == CUSTOM_ESCALATED


def test_one_failure_of_a_custom_job_still_retries_normally(service):
    failed_run(service, "c2", "c2-1")
    recovery, escalation = schedule(service, "c2-1")
    assert recovery["status"] == "scheduled" and escalation is None


def test_a_stalled_custom_site_escalates_after_one_failure(service):
    failed_run(service, "c3", "c3-1", cause="navigation_stalled")
    recovery, escalation = schedule(service, "c3-1")
    assert recovery["status"] == "specialist_scheduled" and escalation["trigger"] == "stalled"


def test_a_custom_site_needs_no_specialist_worker_to_escalate(service):
    off = service.config.model_copy(update={"specialist_workers": 0})
    assert handoff_threshold(off, "custom", STUCK) == 2
    assert handoff_threshold(off, "workday", STUCK) is None  # an ATS job still needs a specialist to move to
    failed_run(service, "c4", "c4-1")
    failed_run(service, "c4", "c4-2")
    recovery, _ = schedule(service, "c4-2", config=off)
    assert recovery["status"] == "specialist_scheduled"


def test_the_escalated_run_is_not_a_standard_failure_and_has_its_own_attempts(service):
    failed_run(service, "c5", "c5-1")
    failed_run(service, "c5", "c5-2")
    schedule(service, "c5-2")
    failed_run(service, "c5", "c5-3", source="c5-2")  # the escalated attempt, routed as the queue would
    with service.db.session() as s:
        assert escalated(s.get(Run, "c5-3")) and not escalated(s.get(Run, "c5-2"))
        assert specialist_attempts(s, "c5") == 1
        # It consumed no standard repair, and the standard runs count only when they came from a recovery source.
        assert automatic_attempts(s, "c5") == 0
    one = service.config.model_copy(update={"specialist_attempts": 1})
    recovery, _ = schedule(service, "c5-3", config=one)
    assert recovery["status"] == "specialist_exhausted" and recovery["scheduled"] is False
    recovery, _ = schedule(service, "c5-3")
    assert recovery["status"] == "specialist_scheduled"


def test_a_failure_waiting_on_an_email_never_escalates(service):
    failed_run(service, "c6", "c6-1")
    failed_run(service, "c6", "c6-2")
    recovery, escalation = schedule(service, "c6-2", waiting_kind="email")
    assert escalation is None and recovery["status"] != "specialist_scheduled"


def test_an_ats_job_keeps_the_specialist_lane(service):
    failed_run(service, "w1", "w1-1", ats="workday")
    failed_run(service, "w1", "w1-2", ats="workday")
    recovery, escalation = schedule(service, "w1-2")
    assert recovery["worker_pool"] == SPECIALIST and "strategy" not in recovery
    with service.db.session() as s:
        assert assign_pool(s, s.get(Job, "w1"), s.get(Run, "w1-2")) == SPECIALIST
        assert assign_strategy(s.get(Job, "w1"), s.get(Run, "w1-2")) == STRATEGY_STANDARD


def test_the_escalated_custom_budget_is_no_larger_unless_the_owner_raises_it(service):
    config = service.config.model_copy(update={"max_model_calls": 18})
    assert pool_boost(config, CUSTOM_ESCALATED) == 1.0
    assert call_limit(config, 1, CUSTOM_ESCALATED) == 18
    more = config.model_copy(update={"custom_escalation_multiplier": 1.5})
    assert call_limit(more, 1, CUSTOM_ESCALATED) == 27 and call_limit(more, 1, CUSTOM) == 18
    assert budget_class({"worker_pool": CUSTOM, "strategy": STRATEGY_ESCALATED}) == CUSTOM_ESCALATED
    assert budget_class({"worker_pool": CUSTOM}) == CUSTOM and budget_class({}) == "normal"


def test_the_queued_event_names_the_custom_escalation():
    note = routing_note(
        CUSTOM,
        CUSTOM,
        {"specialist_escalation": {"trigger": "repeated_failure", "normal_failure_run_ids": ["a", "b"], "cause": STUCK}},
        STRATEGY_ESCALATED,
    )
    assert note == {
        "pool": CUSTOM,
        "strategy": "escalated",
        "specialist_trigger": "repeated_failure",
        "after_failures": 2,
        "failure_cause": STUCK,
    }
    assert routing_note(CUSTOM, CUSTOM, None) == {"pool": CUSTOM}
