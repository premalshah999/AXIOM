"""Retired engines (2026-10-15): drafts named for the Oct 4 engine run on the current engine, and the
model-led agent runs only while runner tiers are enabled (owner retired it on 2026-10-14)."""

from jobpilot.browser import BrowserEngine
from jobpilot.pipeline_comparison import AGENTIC, LEGACY, REASONING, engine_for


def run(pipeline, policy=None):
    packet = {"application_pipeline": pipeline}
    if policy:
        packet["controller_policy"] = policy
    return {"id": "run-1", "packet": packet}


def test_oct04_drafts_continue_on_the_current_engine(service):
    draft = run(LEGACY)
    assert isinstance(engine_for(service, draft), BrowserEngine)
    assert "controller_policy" not in draft["packet"]


def test_agent_drafts_use_the_hybrid_controller_while_tiers_are_off(service):
    service.db.set_setting("runner_tiers", {"enabled": False})
    for pipeline in (AGENTIC, REASONING):
        draft = run(pipeline, "continuous_agent")
        engine_for(service, draft)
        assert draft["packet"]["controller_policy"] == "hybrid"


def test_agent_drafts_keep_the_agent_when_the_owner_enables_tiers(service):
    service.db.set_setting("runner_tiers", {"enabled": True})
    draft = run(AGENTIC)
    engine_for(service, draft)
    assert draft["packet"]["controller_policy"] == "continuous_agent"
