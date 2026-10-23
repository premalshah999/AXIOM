"""L3Harris, Entergy, Farmers and Westinghouse (SuccessFactors, 2026-10-21/22): each employer holds an account for the
applicant's email, refuses the saved password, and mails nothing for a reset ("If you do not receive an email then no
matching account was found"). Every run signed in, failed, and requested another reset. After one unanswered reset the
account is "password unknown": runs stop at once with the owner's one action, and retries skip it."""

import time

import pytest

from jobpilot.adapters.base import PASSWORD_UNKNOWN, Adapter
from jobpilot.autopilot import PERSIST_SKIP
from support_icims import adapter, observation

URL = "https://careers-acme.icims.com/jobs/42/engineer/login"


@pytest.mark.parametrize("minutes_ago,stops", [(20, True), (2, False)])
async def test_an_unanswered_reset_marks_the_password_unknown(service, minutes_ago, stops):
    obs = observation(URL)
    value = adapter(service, obs)
    value.accounts.mark(URL, "reset_requested", {"email": "alex@example.test", "password": "Synthetic9!x"},
                        reset_requested_at=time.time() - minutes_ago * 60)
    value.defer = lambda reason, *a, **k: {"state": k.get("state", "waiting_answer"), "reason": reason}
    value.config.account_password_reset = False  # the ordinary path ends at its first guard
    result = await Adapter.recover(value, obs, "Signing in did not succeed")
    assert (result["reason"] == PASSWORD_UNKNOWN) is stops
    assert (value.accounts.record(URL)["state"] == "password_unknown") is stops


async def test_a_password_unknown_account_stops_without_another_reset(service):
    obs = observation(URL)
    value = adapter(service, obs)
    value.accounts.mark(URL, "password_unknown", {"email": "alex@example.test", "password": "Synthetic9!x"})
    value.defer = lambda reason, *a, **k: {"state": k.get("state"), "reason": reason}
    value.config.account_password_reset = True
    result = await Adapter.recover(value, obs, "Signing in did not succeed")
    assert result == {"state": "waiting_browser", "reason": PASSWORD_UNKNOWN}
    assert PERSIST_SKIP.search(PASSWORD_UNKNOWN)


async def test_successfactors_creates_the_account_once_after_an_unanswered_reset(service, monkeypatch):
    """SuccessFactors says a reset that sends nothing means "no matching account was found". L3Harris (2026-10-21..23)
    requested four resets that never came: the saved login was from a registration that never finished. One Create is
    tried; the state stays settled so no further reset is requested."""
    import re
    from unittest.mock import AsyncMock

    obs = observation(URL, email=False)
    obs["controls"][0].update(aria_label="", automation_id="")
    value = adapter(service, obs)
    value.id = "successfactors"
    value.final = re.compile(r"^Submit Application$", re.I)
    value.accounts.mark(URL, "password_unknown", {"email": "alex@example.test", "password": "Synthetic9!x"})
    value.auth_outcome = AsyncMock(return_value=("bad_credentials", "Invalid email address or password."))
    value.form.scan = AsyncMock(return_value=obs)
    value.switch = AsyncMock(return_value=True)
    recover = AsyncMock(return_value={"state": "waiting_browser", "reason": PASSWORD_UNKNOWN})
    monkeypatch.setattr(Adapter, "recover", recover)
    assert await value.on_sign_in(obs) is None
    value.switch.assert_awaited()
    record = value.accounts.record(URL)
    assert record["state"] == "password_unknown" and record.get("absent_create_at")
    # The Create was tried: a later refusal goes to the owner's action, never another Create or reset.
    value.attempts.clear()
    value.switch.reset_mock()
    await value.on_sign_in(obs)
    value.switch.assert_not_awaited()
    assert value.accounts.record(URL)["state"] == "password_unknown"


async def test_other_platforms_keep_the_owner_action_for_an_unknown_password(service, monkeypatch):
    import re
    from unittest.mock import AsyncMock

    obs = observation(URL, email=False)
    obs["controls"][0].update(aria_label="", automation_id="")
    value = adapter(service, obs)
    value.final = re.compile(r"^Submit Application$", re.I)
    value.accounts.mark(URL, "password_unknown", {"email": "alex@example.test", "password": "Synthetic9!x"})
    value.auth_outcome = AsyncMock(return_value=("bad_credentials", "Invalid password"))
    value.form.scan = AsyncMock(return_value=obs)
    value.switch = AsyncMock(return_value=True)
    monkeypatch.setattr(Adapter, "recover", AsyncMock(return_value={"state": "waiting_browser"}))
    await value.on_sign_in(obs)
    value.switch.assert_not_awaited()
    assert value.accounts.record(URL)["state"] == "password_unknown"  # never overwritten by credentials_rejected
