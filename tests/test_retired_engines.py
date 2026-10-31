"""Retired engines: drafts named for the Oct 4 engine, the reasoning-first pipeline or the agent-led pipeline run on
the current engine and the hybrid controller. The model-led agent was retired on 2026-10-14 and deleted."""

from jobpilot.browser import BrowserEngine
from jobpilot.controller_policy import policy_for
from jobpilot.pipeline_comparison import engine_for


def run(pipeline, policy=None):
    packet = {"application_pipeline": pipeline}
    if policy:
        packet["controller_policy"] = policy
    return {"id": "run-1", "packet": packet}


def test_retired_pipeline_drafts_continue_on_the_current_engine(service):
    for pipeline in ("oct04-825c1b6", "reasoning-first", "agent-led"):
        draft = run(pipeline, "continuous_agent")
        assert isinstance(engine_for(service, draft), BrowserEngine)
        assert policy_for(draft) == "hybrid"
    assert policy_for(run("current", "vision_assisted")) == "vision_assisted"
