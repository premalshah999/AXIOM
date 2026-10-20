"""When an application moves to a specialist, and the larger budget it gets there."""

import pytest

from jobpilot.config import Settings
from jobpilot.db import Event, Job, Run
from jobpilot.recovery import schedule_recovery
from jobpilot.specialist import handoff_threshold, handoff_trigger, pool_boost
from jobpilot.tiers import call_limit, job_over_budget, time_budget

STUCK = "control_observation"


def settings(**values):
    return Settings(_env_file=None, **values)


@pytest.mark.parametrize(
    ("ats", "cause", "needed"),
    [
        ("workday", STUCK, 2),  # an ordinary failure on an ordinary platform: retry once on an ATS worker first
        ("successfactors", STUCK, 1),  # known-hard platform
        ("SAP", STUCK, 1),  # names are normalized
        ("workday", "navigation_stalled", 1),  # a stalled controller will not be fixed by the same strategy
        ("custom", "model_output", 2),
    ],
)
def test_how_many_failures_send_a_job_to_a_specialist(ats, cause, needed):
    assert handoff_threshold(settings(), ats, cause) == needed


def test_every_rule_is_configurable():
    assert handoff_threshold(settings(specialist_on_stall=False), "workday", "navigation_stalled") == 2
    assert handoff_threshold(settings(specialist_hard_ats=""), "successfactors", STUCK) == 2
    assert handoff_threshold(settings(specialist_hard_ats="workday"), "workday", STUCK) == 1
    assert handoff_threshold(settings(specialist_after_failures=3), "workday", STUCK) == 3


def test_without_specialists_nothing_is_ever_handed_over():
    config = settings(specialist_workers=0)
    assert handoff_threshold(config, "successfactors", "navigation_stalled") is None
    assert handoff_trigger(config, "successfactors", "navigation_stalled", 5) is None


def test_the_recorded_trigger_names_the_reason():
    config = settings()
    assert handoff_trigger(config, "workday", STUCK, 1) is None
    assert handoff_trigger(config, "workday", STUCK, 2) == "repeated_failure"
    assert handoff_trigger(config, "eightfold", STUCK, 1) == "hard_ats"
    assert handoff_trigger(config, "workday", "navigation_stalled", 1) == "stalled"


def test_specialists_get_a_larger_budget_than_ats_workers():
    config = settings()
    assert pool_boost(config, "normal") == 1.0 and pool_boost(config, None) == 1.0
    assert pool_boost(config, "specialist") == 1.5
    # Wall clock: 8 minutes becomes 12; the 20-minute ceiling scales too.
    assert time_budget(480, 1, "oracle") == 480
    assert time_budget(480, 1, "oracle", boost=1.5) == 720
    assert time_budget(1000, 2, boost=1.5) == 1800 and time_budget(1000, 2) == 1200
    # Model calls: 18 becomes 27, and a runner tier's own larger cap scales as well.
    assert call_limit(config, 1) == 18 and call_limit(config, 1, "normal") == 18
    assert call_limit(config, 1, "specialist") == 27
    assert call_limit(config, 2, "specialist") == 60
    assert call_limit(settings(specialist_budget_multiplier=2), 1, "specialist") == 36


def test_the_per_job_token_cap_scales_for_specialists(service):
    with service.db.session() as s:
        s.add(Job(id="j", identity="j", url="https://j.example.test/1", ats="workday", status="waiting_browser",
                  company="j", title="Engineer"))
        s.add(Run(id="r", job_id="j", state="failed", mode="submit", packet={}))
        s.add(Event(run_id="r", kind="model_call", message="x", data={"prompt_tokens": 2_000_000}))
    with service.db.session() as s:
        assert job_over_budget(s, service.db, "j") is True  # 2.0M >= the 1.6M default cap
        assert job_over_budget(s, service.db, "j", boost=1.5) is False  # 2.0M < 2.4M
    with service.db.session() as s:
        s.add(Event(run_id="r", kind="model_call", message="y", data={"prompt_tokens": 500_000}))
        assert job_over_budget(s, service.db, "j", boost=1.5) is True


def failed_run(service, job_id, run_id, ats, *, cause=STUCK, pool="normal", state="waiting_browser"):
    packet = {
        "match_approved": True,
        "profile": {"autonomous": True},
        "worker_pool": pool,
        "recovery_cause": cause,
        "blocker_type": "observation_failure" if cause == "navigation_stalled" else "control_failure",
        "browser_checkpoint": {"resume_sha": "abc", "failed_strategies": {"fill:email": {"attempts": [{"state": "failed"}]}}},
    }
    with service.db.session() as s:
        if not s.get(Job, job_id):
            s.add(Job(id=job_id, identity=job_id, url=f"https://{job_id}.example.test/jobs/1", ats=ats,
                      status="waiting_browser", company=job_id, title="Engineer"))
        s.add(Run(id=run_id, job_id=job_id, state=state, mode="submit", reason="Browser did not advance", packet=packet))
    return packet


def schedule(service, run_id, packet, config=None, **result):
    result = {"recovery_cause": packet["recovery_cause"], "blocker_type": packet["blocker_type"],
              "browser_checkpoint": packet["browser_checkpoint"], "reason": "Browser did not advance", **result}
    with service.db.session() as s:
        run = s.get(Run, run_id)
        schedule_recovery(s, config or service.config, run, result)
        escalation = run.packet.get("specialist_escalation")
        job = s.get(Job, run.job_id)
    return service.db.get_setting("draft_recovery:" + job.id), escalation


def test_one_failure_on_a_known_hard_platform_goes_to_a_specialist(service):
    packet = failed_run(service, "sf", "sf-1", "successfactors")
    recovery, escalation = schedule(service, "sf-1", packet)
    assert recovery["status"] == "specialist_scheduled" and recovery["scheduled"] is True
    assert escalation["eligible"] is True and escalation["trigger"] == "hard_ats"
    assert escalation["attempt_limit"] == 2


def test_one_ordinary_failure_retries_on_an_ats_worker(service):
    packet = failed_run(service, "wd", "wd-1", "workday")
    recovery, escalation = schedule(service, "wd-1", packet)
    assert recovery["status"] == "scheduled" and escalation is None


def test_the_second_failure_goes_to_a_specialist(service):
    failed_run(service, "wd2", "wd2-1", "workday")
    packet = failed_run(service, "wd2", "wd2-2", "workday")
    recovery, escalation = schedule(service, "wd2-2", packet)
    assert recovery["status"] == "specialist_scheduled"
    assert escalation["trigger"] == "repeated_failure"
    assert sorted(escalation["normal_failure_run_ids"]) == ["wd2-1", "wd2-2"]


def test_a_stalled_controller_goes_to_a_specialist_after_one_failure(service):
    packet = failed_run(service, "st", "st-1", "workday", cause="navigation_stalled")
    recovery, escalation = schedule(service, "st-1", packet)
    assert recovery["status"] == "specialist_scheduled" and escalation["trigger"] == "stalled"


def test_with_no_specialists_a_hard_platform_retries_on_ats_workers(service):
    packet = failed_run(service, "off", "off-1", "successfactors")
    recovery, escalation = schedule(service, "off-1", packet, config=service.config.model_copy(update={"specialist_workers": 0}))
    assert recovery["status"] == "scheduled" and escalation is None


def test_waiting_on_an_email_never_goes_to_a_specialist(service):
    packet = failed_run(service, "em", "em-1", "successfactors")
    recovery, escalation = schedule(service, "em-1", packet, waiting_kind="email")
    assert escalation is None and recovery["status"] != "specialist_scheduled"


def test_a_job_that_used_up_its_specialist_attempts_is_left_alone(service):
    failed_run(service, "cap", "cap-1", "workday")
    packet = failed_run(service, "cap", "cap-2", "workday", pool="specialist")
    one = service.config.model_copy(update={"specialist_attempts": 1})
    recovery, _ = schedule(service, "cap-2", packet, config=one)
    assert recovery["status"] == "specialist_exhausted" and recovery["scheduled"] is False
    recovery, _ = schedule(service, "cap-2", packet)
    assert recovery["status"] == "specialist_scheduled"
