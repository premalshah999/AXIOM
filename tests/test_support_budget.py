"""The load stretch on test budgets and Playwright ceilings: nothing changes on an idle machine."""

from playwright._impl import _connection

import support_budget


def load(monkeypatch, runnable, cores=10):
    monkeypatch.setattr(support_budget.os, "getloadavg", lambda: (runnable, 0, 0))
    monkeypatch.setattr(support_budget.os, "cpu_count", lambda: cores)


def test_an_idle_machine_stretches_nothing(monkeypatch):
    load(monkeypatch, 2)
    assert support_budget.scaled(5) == 5
    assert _connection._augment_params({"timeout": 1500}, None)["timeout"] == 1500


def test_a_busy_machine_stretches_a_ceiling_up_to_the_most(monkeypatch):
    load(monkeypatch, 20)
    assert support_budget.scaled(1) == 6
    assert _connection._augment_params({"timeout": 1500}, None)["timeout"] == 9000
    load(monkeypatch, 500)
    assert support_budget.load_factor() == support_budget.MOST


def test_no_limit_and_a_sleep_are_left_alone(monkeypatch):
    load(monkeypatch, 20)
    assert _connection._augment_params({"timeout": 0}, None)["timeout"] == 0
    assert _connection._augment_params({"waitTimeout": 500}, None) == {"waitTimeout": 500}
