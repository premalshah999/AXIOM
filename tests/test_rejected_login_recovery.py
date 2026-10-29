from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from jobpilot.adapters.base import Adapter
from support_icims import adapter, observation

URL = "https://careers-acme.icims.com/jobs/42/engineer/login"
LOGIN = {"email": "alex@example.test", "password": "Synthetic9!x"}


@pytest.mark.parametrize("change", ["none", "username", "password", "legacy"])
async def test_recent_explicit_rejection_of_known_login_avoids_repeated_sign_in(service, monkeypatch, change):
    obs = observation(URL)
    obs["fields"][0].update(type="text", label="User Name", automation_id="")
    obs["fields"][1]["automation_id"] = ""
    obs["controls"][0].update(aria_label="", automation_id="")
    value = adapter(service, obs)
    value.id = "custom"
    value.e.profile = SimpleNamespace(account_username="original-user")
    value.accounts.mark(URL, "authenticated", LOGIN)
    value.auth_outcome = AsyncMock(return_value=("bad_credentials", "Invalid password"))
    recovery = AsyncMock(return_value={"state": "waiting_browser"})
    monkeypatch.setattr(Adapter, "recover", recovery)
    await Adapter.authenticate(value, obs, "sign_in")
    assert value.click.await_count == 1
    record = value.accounts.record(URL)
    if change == "username":
        value.e.profile.account_username = "new-user"
    elif change == "password":
        value.accounts.mark(URL, "credentials_rejected", {**LOGIN, "password": "Another9!x"})
    elif change == "legacy":
        record.pop("rejected_login_context")
        service.db.set_setting(value.accounts.store.key(URL), record)
    value.attempts.clear()
    await Adapter.authenticate(value, obs, "sign_in")
    assert value.click.await_count == (1 if change == "none" else 2)
