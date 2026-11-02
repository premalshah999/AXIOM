"""A page that commits empty and draws itself a moment later is waited for (16 runs on six platforms since 10-12 ended "Browser observation stopped responding" after about
five seconds), and SuccessFactors' login is read again while it finishes redirecting (Farmers, 2026-11-01)."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

from playwright.async_api import Error as PlaywrightError

from jobpilot import browser
from jobpilot.browser import BrowserEngine

EMPTY = {"fields": [], "controls": [], "text": "", "title": ""}
DRAWN = {"fields": [{"id": "a", "label": "First name", "type": "text"}], "controls": [{"id": "b", "label": "Next"}], "text": "Step 2", "title": "Apply"}


def engine(scans):
    value = BrowserEngine.__new__(BrowserEngine)
    value.armed = False
    value.result = None
    value.recover_oracle_job_shell = AsyncMock(return_value=False)
    value.form = SimpleNamespace(settle=AsyncMock(), scan=AsyncMock(side_effect=scans), ledger={"a": {"verified": True}})
    return value


async def test_a_page_that_draws_itself_after_seconds_is_read(monkeypatch):
    monkeypatch.setattr(browser, "OBSERVATION_WAIT_SECONDS", 30)
    monkeypatch.setattr(browser.asyncio, "sleep", AsyncMock())
    value = engine([EMPTY, EMPTY, PlaywrightError("Execution context was destroyed"), EMPTY, EMPTY, DRAWN])
    assert await value.recover_empty_observation(EMPTY) == DRAWN
    assert value.result is None


async def test_a_page_that_never_draws_still_stops_the_run(monkeypatch):
    monkeypatch.setattr(browser, "OBSERVATION_WAIT_SECONDS", 0)
    value = engine([EMPTY] * 4)
    await value.recover_empty_observation(EMPTY)
    assert value.result["blocker_type"] == "observation_failure"


async def test_a_page_after_the_commit_step_is_never_waited_on_or_reopened():
    value = engine([])
    value.armed = True
    await value.recover_empty_observation(EMPTY)
    assert value.result["state"] == "submission_unknown"
    value.form.scan.assert_not_awaited()


async def test_the_successfactors_login_is_read_again_while_it_redirects(monkeypatch):
    monkeypatch.setattr(browser.asyncio, "sleep", AsyncMock())
    sign_in = {**EMPTY, "fields": [{"id": "e", "label": "Email", "type": "text"}], "controls": [{"id": "s", "label": "Sign In"}]}
    blank = {"fields": [], "controls": [], "text": ""}
    value = BrowserEngine.__new__(BrowserEngine)
    value.emit = lambda *a, **k: None
    value.page = SimpleNamespace(url="https://career2.successfactors.eu/careers?company=ExampleCo", goto=AsyncMock())
    value.form = SimpleNamespace(settle=AsyncMock(), scan=AsyncMock(side_effect=[*[blank] * 8, PlaywrightError("Execution context was destroyed"), blank, sign_in]))
    assert await value.await_successfactors_handoff(blank) == sign_in
    assert "loginFlowRequired=true" in value.page.goto.await_args.args[0]
