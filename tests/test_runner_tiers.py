from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from jobpilot import tiers
from jobpilot.application_budget import HOST_OVERLOADED
from jobpilot.db import Event, Job, Run


def seed(
    service,
    name,
    *,
    state="waiting_browser",
    tier=1,
    reason="Browser did not advance",
    ats="workday",
    extra_runs=(),
    submitting=False,
    job_status="waiting_browser",
):
    old = (datetime.now(UTC) - timedelta(minutes=10)).isoformat()
    with service.db.session() as s:
        s.add(
            Job(
                id=name,
                identity=name,
                url=f"https://fixture.test/{name}",
                ats=ats,
                status=job_status,
                company=name,
                title="Engineer",
            )
        )
        s.add(
            Run(
                id=name + "-1",
                job_id=name,
                state=state,
                mode="submit",
                reason=reason,
                created_at=old,
                finished_at=old,
                packet={"runner_tier": tier, "match_approved": True},
            )
        )
        for i, extra in enumerate(extra_runs):
            s.add(
                Run(
                    id=f"{name}-x{i}",
                    job_id=name,
                    state=extra,
                    mode="submit",
                    created_at=old,
                    finished_at=old,
                    packet={},
                )
            )
        if submitting:
            s.add(Event(run_id=name + "-1", kind="submitting", message="x", data={}, created_at=old))


async def run_escalation(service, monkeypatch):
    manage = AsyncMock(return_value={"state": "queued"})
    monkeypatch.setattr("jobpilot.job_control.manage", manage)
    count = await tiers.escalate_due(service)
    return count, [c.args[1] for c in manage.await_args_list]


async def test_failed_run_escalates_one_tier_once(service, monkeypatch):
    seed(service, "a")
    count, jobs = await run_escalation(service, monkeypatch)
    assert (count, jobs) == (1, ["a"])
    assert tiers.job_tier(service.db, "a") == 2
    # The same failure is never escalated twice.
    assert await run_escalation(service, monkeypatch) == (0, [])


async def test_tier_two_failure_goes_to_tier_three_then_stops(service, monkeypatch):
    seed(service, "b", tier=2)
    assert (await run_escalation(service, monkeypatch))[1] == ["b"]
    assert tiers.job_tier(service.db, "b") == 3
    seed(service, "c", tier=3)
    assert await run_escalation(service, monkeypatch) == (0, [])


async def test_unsafe_or_external_failures_are_not_escalated(service, monkeypatch):
    seed(service, "closed", reason="The posting is closed or no longer available")
    seed(service, "mail", reason="Waiting for the employer verification email")
    seed(service, "won", extra_runs=("confirmed",))
    seed(service, "unk", extra_runs=("submission_unknown",))
    seed(service, "sent", submitting=True)
    seed(service, "icims", ats="icims")
    service.db.set_setting("ats_focus", {"exclude": ["icims"]})
    assert await run_escalation(service, monkeypatch) == (0, [])


async def test_blocked_queue_leaves_the_job_for_a_later_tick(service, monkeypatch):
    seed(service, "d")
    monkeypatch.setattr("jobpilot.job_control.manage", AsyncMock(side_effect=RuntimeError("pool busy")))
    assert await tiers.escalate_due(service) == 0
    assert tiers.job_tier(service.db, "d") == 1
    assert (await run_escalation(service, monkeypatch))[1] == ["d"]


def test_budgets_grow_with_tier(service):
    cfg = service.config
    assert tiers.call_limit(cfg, 1) == cfg.max_model_calls
    assert tiers.call_limit(cfg, 3) > tiers.call_limit(cfg, 2) >= cfg.max_model_calls
    assert tiers.agent_steps(3) > tiers.agent_steps(2) > tiers.agent_steps(1)
    assert tiers.time_budget(600, 1) == 600 and tiers.time_budget(600, 3) == 1200
    assert tiers.effort_for(1, "low") == "low" and tiers.effort_for(3, "low") == "high"


def test_higher_tier_turns_reasoning_on(service, monkeypatch):
    cfg = service.config.model_copy(update={"llm_provider": "deepseek"})
    monkeypatch.setattr(tiers, "tier_of", lambda db, run_id: 2)
    assert tiers.apply_model(service.db, cfg, "r", "disabled", 20) == (2, "enabled", 75)
    monkeypatch.setattr(tiers, "tier_of", lambda db, run_id: 1)
    assert tiers.apply_model(service.db, cfg, "r", "disabled", 20) == (1, "disabled", 20)


async def test_standing_protection_marks_the_failure_handled(service, monkeypatch):
    from jobpilot.service import QueueBlocked

    seed(service, "capped")
    seed(service, "fresh")
    calls = []

    async def manage(svc, job_id, action, **kw):
        calls.append(job_id)
        if job_id == "capped":
            raise QueueBlocked(
                "Employer already has three confirmed or reserved applications", code="employer_cap"
            )
        return {"state": "queued"}

    monkeypatch.setattr("jobpilot.job_control.manage", manage)
    assert await tiers.escalate_due(service) == 1
    assert tiers.job_tier(service.db, "capped") == 1  # not advanced
    calls.clear()
    # The capped job is not retried every minute and never blocks the next job.
    assert await tiers.escalate_due(service) == 0 and calls == []


def test_transport_keeps_tier_reasoning_despite_global_override(service):
    from jobpilot.models import MeteredTransport

    service.db.set_setting("model_reasoning", {"deepseek_thinking": "disabled"})
    cfg = service.config.model_copy(update={"llm_provider": "deepseek"})
    assert MeteredTransport(service.db, cfg, None, thinking="disabled", tier=1).thinking == "disabled"
    assert MeteredTransport(service.db, cfg, None, thinking="disabled", tier=2).thinking == "enabled"


def test_tier_one_is_the_cheap_engine_and_higher_tiers_use_the_agent(service):
    from jobpilot.db import Setting
    from jobpilot.pipeline_comparison import AGENTIC, CURRENT, LEGACY, assign

    job = Job(id="legacy", identity="legacy", url="https://fixture.test/legacy")

    def run(pipeline, **packet):
        return Run(
            id="r",
            job_id="legacy",
            state="waiting_browser",
            mode="submit",
            packet={"application_pipeline": pipeline, **packet},
        )

    with service.db.session() as s:
        # A job once handed to the agent (or the frozen Oct-4 engine) returns to the cheap engine.
        assert assign(s, job, run(AGENTIC)) == CURRENT
        assert assign(s, job, run(LEGACY)) == CURRENT
        assert assign(s, job, run(CURRENT)) == CURRENT
        # Receipt verification keeps the engine that submitted.
        assert assign(s, job, run(LEGACY, verification_only=True)) == LEGACY
        s.add(Setting(key="runner_tier:legacy", value={"tier": 2}))
        s.flush()
        assert assign(s, job, run(CURRENT)) == AGENTIC
        assert assign(s, job, run(LEGACY)) == AGENTIC
        # Owner cost control: with runner tiers disabled, nothing runs the model-led agent.
        s.add(Setting(key="runner_tiers", value={"enabled": False}))
        s.flush()
        assert assign(s, job, run(CURRENT)) == CURRENT
        assert assign(s, job, run(CURRENT, specialist_escalation={"eligible": True})) == CURRENT


def test_job_token_cap_counts_recent_model_input(service):
    from jobpilot.db import Event

    seed(service, "big")
    seed(service, "small")
    recent = datetime.now(UTC).isoformat()
    old = (datetime.now(UTC) - timedelta(hours=30)).isoformat()
    with service.db.session() as s:
        for job, tokens, at in [
            ("big", 900_000, recent),
            ("big", 800_000, recent),
            ("small", 100_000, recent),
            ("small", 5_000_000, old),
        ]:
            s.add(
                Event(
                    run_id=job + "-1",
                    kind="model_call",
                    message="x",
                    data={"prompt_tokens": tokens},
                    created_at=at,
                )
            )
    with service.db.session() as s:
        assert tiers.job_over_budget(s, service.db, "big")  # 1.7M in 24h
        assert not tiers.job_over_budget(s, service.db, "small")  # the 30-hour-old call does not count
    service.db.set_setting("runner_tiers", {"job_token_cap": 5_000_000})
    with service.db.session() as s:
        assert not tiers.job_over_budget(s, service.db, "big")  # the owner can raise the cap


async def test_over_budget_job_is_not_escalated(service, monkeypatch):
    from jobpilot.db import Event

    seed(service, "spent")
    with service.db.session() as s:
        s.add(
            Event(
                run_id="spent-1",
                kind="model_call",
                message="x",
                data={"prompt_tokens": 2_000_000},
                created_at=datetime.now(UTC).isoformat(),
            )
        )
    assert await run_escalation(service, monkeypatch) == (0, [])


def test_workday_gets_no_budget_extension():
    assert tiers.time_budget(900, 1, "workday") == 900
    assert tiers.time_budget(900, 3, "workday") == 1200


def test_deepseek_cache_hits_are_priced_at_the_cache_rate(service):
    from jobpilot.models import token_cost

    cfg = service.config.model_copy(update={"llm_provider": "deepseek"})
    full = token_cost(cfg, 1_000_000, 0)
    cached = token_cost(cfg, 1_000_000, 0, cache_hit_tokens=900_000)
    assert abs(full - 0.3) < 1e-9
    assert abs(cached - (0.1 * 0.3 + 0.9 * 0.03)) < 1e-9


@pytest.mark.parametrize(
    "reason, escalated",
    [
        ("Browser did not advance", True),
        (HOST_OVERLOADED + " and the application ran out of time before anything was sent", False),
        ("Worker interrupted before submission; draft recovery scheduled", False),
        ("Open the ApplyPilot Chrome window: the browser is responding", False),
    ],
)
def test_tier_escalation_skips_runs_that_sent_nothing(service, reason, escalated):
    """A deploy, a dead Chrome or an overloaded host stopped these before the employer saw anything; a stronger
    runner (more calls, thinking on) cannot fix that and only spends one of the three tiers."""
    old = (datetime.now(UTC) - timedelta(minutes=10)).isoformat()
    with service.db.session() as s:
        s.add(Job(id="tier", identity="tier", url="https://fixture.test/tier", ats="workday",
                  status="waiting_browser", company="Tier", title="Engineer"))
        s.add(Run(id="tier-1", job_id="tier", state="waiting_browser", mode="submit", reason=reason,
                  created_at=old, finished_at=old, packet={"runner_tier": 1, "match_approved": True}))
    found = [job_id for _, job_id, _ in tiers._candidates(service.db, 4)]
    assert (found == ["tier"]) is escalated


def test_the_new_gate_codes_wait_instead_of_marking_a_job_handled():
    """host_health.check refuses with these codes while it lasts; escalation must retry later, not give up on the job."""
    from jobpilot.tiers import TRANSIENT

    assert {"draining", "browser_unavailable", "browser_probe", "host_overloaded", "host_loaded"} <= TRANSIENT
