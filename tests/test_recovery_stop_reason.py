import json
from types import SimpleNamespace

from jobpilot.browser import BrowserEngine


def engine(problems=(), saved=None):
    e = object.__new__(BrowserEngine)
    e.form = SimpleNamespace(last_problems=list(problems), failed_strategies={"__recovery_blocker__": saved or {}})
    return e


def test_exhaustion_names_current_field_problem_over_old_action():
    reason = engine(["Major/Field of Study is required"]).recovery_exhausted_reason(
        [{"outcome": json.dumps({"reason": "Old navigation error"})}])
    assert "Major/Field of Study is required" in reason
    assert "Old navigation" not in reason


def test_exhaustion_retains_last_action_refusal():
    reason = engine().recovery_exhausted_reason([
        {"outcome": json.dumps({"verified": False, "reason": "Final submit control was not unique"})}])
    assert "Final submit control was not unique" in reason


def test_successful_action_clears_older_refusal():
    reason = engine().recovery_exhausted_reason([
        {"outcome": json.dumps({"reason": "Old navigation error"})},
        {"outcome": json.dumps({"verified": True})}])
    assert "Old navigation" not in reason


def test_exhaustion_redacts_private_data_and_bounds_reason():
    reason = engine(["Contact alex@example.test " + "x" * 2000]).recovery_exhausted_reason([])
    assert "alex@example.test" not in reason
    assert len(reason) < 650
