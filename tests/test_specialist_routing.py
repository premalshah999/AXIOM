"""Review of the specialist routing (2026-10-20): admission, budgets and the recorded reason all follow the pool a run
is actually given, and every route (handoff, owner resume, a busy specialist pool) says why in the run's timeline."""

from types import SimpleNamespace

from jobpilot.answer_quality import advice_budget
from jobpilot.config import Settings


def test_the_optional_audit_keeps_two_calls_of_the_runs_own_cap():
    config = Settings(_env_file=None)  # 18 calls, 27 for a specialist
    ordinary = SimpleNamespace(model_calls=16, packet={})
    specialist = SimpleNamespace(model_calls=16, packet={"worker_pool": "specialist"})
    assert advice_budget(config, ordinary) is False  # 16 of 18 leaves exactly the two for the owner agents
    assert advice_budget(config, specialist) is True
    assert advice_budget(config, SimpleNamespace(model_calls=25, packet={"worker_pool": "specialist"})) is False
    assert advice_budget(config, None) is True
