"""Review of the specialist routing (2026-10-20): admission, budgets and the recorded reason all follow the pool a run
is actually given, and every route (handoff, owner resume, a busy specialist pool) says why in the run's timeline."""

from types import SimpleNamespace

import pytest

from jobpilot.admission import QueueBlocked
from jobpilot.answer_quality import advice_budget
from jobpilot.config import Settings
from jobpilot.db import Event, Job, Run
from jobpilot.pipeline_comparison import continued_run, routed_pool
from jobpilot.specialist import routing_note

ESCALATION = {
    "eligible": True,
    "trigger": "hard_ats",
    "cause": "navigation_stalled",
    "normal_failure_run_ids": ["a", "b"],
}


def run(rid, state, created, packet=None):
    return SimpleNamespace(id=rid, state=state, created_at=created, packet=packet or {})


def test_a_run_continues_from_its_recovery_source_else_the_newest_waiting_run():
    runs = [run("old", "waiting_browser", "1"), run("new", "timed_out", "3"), run("done", "confirmed", "4")]
    source = run("src", "waiting_browser", "0")
    assert continued_run(runs, source) is source
    assert continued_run(runs).id == "new"  # a confirmed run is never continued
    assert continued_run([run("x", "failed", "1")]) is None


def seed(service, *, escalated, tokens=2_000_000, state="waiting_browser"):
    packet = {"worker_pool": "specialist", "specialist_escalation": ESCALATION} if escalated else {}
    with service.db.exclusive() as s:
        s.add(
            Job(
                id="job",
                identity="job",
                url="https://careers.example.test/job/1",
                ats="workday",
                status="ready",
                title="Software Engineer",
            )
        )
        s.add(Run(id="last", job_id="job", mode="submit", state=state, packet=packet))
        s.add(Event(run_id="last", kind="model_call", message="x", data={"prompt_tokens": tokens}))


def test_the_pool_a_job_will_take_is_known_before_the_run_exists(service):
    seed(service, escalated=True)
    with service.db.session() as s:
        job = s.get(Job, "job")
        assert routed_pool(s, job) == "specialist"  # an owner's Apply continues the specialist's run
        assert routed_pool(s, job, "last") == "specialist"
    seed_other = Run(id="plain", job_id="job", mode="submit", state="waiting_browser", packet={})
    with service.db.exclusive() as s:
        s.add(seed_other)
    with service.db.session() as s:
        assert routed_pool(s, s.get(Job, "job"), "plain") == "normal"  # an explicit source decides


async def test_an_owner_resume_of_a_specialist_job_gets_the_specialist_token_cap(service):
    """2.0M tokens is over the ATS workers' 1.6M cap and under the specialist's 2.4M. Admission looked only at the
    recovery source, so an owner's Apply was refused here although its run would have been a specialist's."""
    seed(service, escalated=True)
    service.db.set_setting("control", {"paused": False, "auto_submit": True})
    with pytest.raises(ValueError, match="Choose an uploaded resume") as reached:
        await service.queue("job", "submit", retry=True)
    assert not isinstance(reached.value, QueueBlocked)  # past the budget gate, stopped by the missing resume


async def test_the_same_job_on_an_ats_worker_is_still_refused_at_the_ordinary_cap(service):
    seed(service, escalated=False)
    with pytest.raises(QueueBlocked) as blocked:
        await service.queue("job", "submit", retry=True)
    assert blocked.value.code == "job_budget"


def test_the_queued_event_names_the_trigger_of_a_handoff():
    note = routing_note("specialist", "specialist", {"specialist_escalation": ESCALATION})
    assert note == {
        "pool": "specialist",
        "specialist_trigger": "hard_ats",
        "after_failures": 2,
        "failure_cause": "navigation_stalled",
    }


def test_a_busy_specialist_pool_is_recorded_as_a_fallback_not_hidden():
    note = routing_note("specialist", "normal", {"specialist_escalation": ESCALATION})
    assert note["pool"] == "normal" and note["specialist_trigger"] == "hard_ats"
    assert "regular budget" in note["specialist_fallback"]


def test_an_ordinary_run_records_only_its_pool():
    assert routing_note("normal", "normal", None) == {"pool": "normal"}
    assert routing_note("specialist", "specialist", {})["specialist_trigger"] == "unrecorded"


def test_the_optional_audit_keeps_two_calls_of_the_runs_own_cap():
    config = Settings(_env_file=None)  # 18 calls, 27 for a specialist
    ordinary = SimpleNamespace(model_calls=16, packet={})
    specialist = SimpleNamespace(model_calls=16, packet={"worker_pool": "specialist"})
    assert advice_budget(config, ordinary) is False  # 16 of 18 leaves exactly the two for the owner agents
    assert advice_budget(config, specialist) is True
    assert advice_budget(config, SimpleNamespace(model_calls=25, packet={"worker_pool": "specialist"})) is False
    assert advice_budget(config, None) is True
