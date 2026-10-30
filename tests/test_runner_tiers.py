from datetime import UTC, datetime, timedelta

from jobpilot import tiers
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


def test_budgets_grow_with_tier(service):
    cfg = service.config
    assert tiers.call_limit(cfg, 1) == cfg.max_model_calls
    assert tiers.call_limit(cfg, 3) > tiers.call_limit(cfg, 2) >= cfg.max_model_calls
    assert tiers.time_budget(600, 1) == 600 and tiers.time_budget(600, 3) == 1200
    assert tiers.effort_for(1, "low") == "low" and tiers.effort_for(3, "low") == "high"


def test_higher_tier_turns_reasoning_on(service, monkeypatch):
    cfg = service.config.model_copy(update={"llm_provider": "deepseek"})
    monkeypatch.setattr(tiers, "tier_of", lambda db, run_id: 2)
    assert tiers.apply_model(service.db, cfg, "r", "disabled", 20) == (2, "enabled", 75)
    monkeypatch.setattr(tiers, "tier_of", lambda db, run_id: 1)
    assert tiers.apply_model(service.db, cfg, "r", "disabled", 20) == (1, "disabled", 20)


def test_transport_keeps_tier_reasoning_despite_global_override(service):
    from jobpilot.models import MeteredTransport

    service.db.set_setting("model_reasoning", {"mimo_thinking": "disabled"})
    cfg = service.config.model_copy(update={"llm_provider": "mimo"})
    assert MeteredTransport(service.db, cfg, None, thinking="disabled", tier=1).thinking == "disabled"
    assert MeteredTransport(service.db, cfg, None, thinking="disabled", tier=2).thinking == "enabled"


def test_deepseek_never_reasons_at_any_tier_or_override(service):
    # Owner 2026-10-27: DeepSeek is used without its reasoning mode.
    from jobpilot.models import MeteredTransport

    service.db.set_setting("model_reasoning", {"deepseek_thinking": "enabled", "thinking": "enabled"})
    cfg = service.config.model_copy(update={"llm_provider": "deepseek"})
    for tier in (1, 2, 3):
        assert MeteredTransport(service.db, cfg, None, thinking="enabled", tier=tier).thinking == "disabled"


def test_every_new_run_uses_the_current_engine(service):
    from jobpilot.db import Setting
    from jobpilot.pipeline_comparison import CURRENT, assign

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
        # A job once handed to the retired agent (or the frozen Oct-4 engine) returns to the current engine.
        for retired in ("agent-led", "reasoning-first", "oct04-825c1b6"):
            assert assign(s, job, run(retired)) == CURRENT
        assert assign(s, job, run(CURRENT)) == CURRENT
        # Receipt verification keeps the engine that submitted.
        assert assign(s, job, run("oct04-825c1b6", verification_only=True)) == "oct04-825c1b6"
        # A raised runner tier or a specialist escalation no longer hands the job to a model-led agent.
        s.add(Setting(key="runner_tier:legacy", value={"tier": 2}))
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

