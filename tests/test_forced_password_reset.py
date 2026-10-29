from unittest.mock import AsyncMock

import pytest

from jobpilot.adapters.base import Step
from test_one_page_reset import SITE, SIGN_IN, adapter, control, field, page


def reset_page():
    return page("/login/password/token", [field("pw", "New password", "password"),
                                        field("confirm", "Confirm new password", "password")], [],
                "Please set up your new credentials in order to log in. Your new password must: "
                "Have at least 8 characters. Be different from your previous 5 passwords.", ())


def test_only_explicit_new_password_change_is_classified_as_reset(service):
    value = adapter(service)
    obs = reset_page()
    assert value.classify_page(obs) == Step.NEW_PASSWORD
    obs["text"] = "Create an account. Choose a new password."
    assert value.classify_page(obs) == Step.CREATE_ACCOUNT


@pytest.mark.parametrize("outcome", ["ok", "password_rules"])
async def test_forced_reset_uses_fresh_password_and_saves_it_only_on_success(service, outcome):
    value = adapter(service)
    obs = reset_page()
    value.form.frames["confirm"] = value.form.frames["pw"]
    value.accounts.mark(SITE + "/reset-password", "authenticated", {"email": "alex@example.test", "password": "OldPass9!"})
    value.accounts.store.generated = lambda length: "FreshPass9!abcd"
    ready = {**obs, "controls": [control("go", "Next")]}
    value.form.scan.side_effect = [ready, SIGN_IN]
    value.auth_outcome = AsyncMock(return_value=(outcome, "Password rejected"))
    value.classify = lambda _: Step.SIGN_IN
    result = await value.on_new_password(obs)
    value.locators["pw"].press_sequentially.assert_awaited_once_with("FreshPass9!abcd", delay=25, timeout=15000)
    value.locators["confirm"].press_sequentially.assert_awaited_once_with("FreshPass9!abcd", delay=25, timeout=15000)
    value.click.assert_awaited_once()
    saved = value.accounts.saved(SITE + "/reset-password")
    assert saved["password"] == ("FreshPass9!abcd" if outcome == "ok" else "OldPass9!")
    assert (result is None) == (outcome == "ok")
