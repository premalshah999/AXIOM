"""iCIMS Central (By Light, 2026-10-24..27): "Your account has been blocked after multiple consecutive login attempts.
Reset your password to unblock your account". Auth0's brute-force block is lifted by a new password, but the run parked
"The employer locked the account after failed sign-ins", the autopilot never retried it, and every manual retry met the
same block. A lock whose page offers the reset that lifts it goes to the mailbox reset; any other lock still parks."""

import re
from unittest.mock import AsyncMock

import pytest

from jobpilot.adapters.base import RESET_UNLOCKS, Adapter
from support_icims import adapter, observation

URL = "https://careers-acme.icims.com/jobs/42/engineer/login"
AUTH0 = (
    "Your account has been blocked after multiple consecutive login attempts. Reset your password to unblock your account"
)


def locked_page(error, *, reset=True):
    obs = observation(URL, email=False, label="LOG IN")
    obs["controls"][0].update(aria_label="", automation_id="")
    if reset:
        obs["controls"].append({"id": "forgot", "label": "Reset your password", "href": "", "aria_label": "",
                                "automation_id": ""})
    obs["errors"] = [error]
    return obs


def signing_in(service, monkeypatch, page):
    value = adapter(service, page)
    value.final = re.compile(r"^Submit Application$", re.I)
    value.auth_outcome = AsyncMock(return_value=("locked", "account has been blocked"))
    value.form.scan = AsyncMock(return_value=page)
    value.defer = lambda reason, *a, **k: {"state": k.get("state"), "reason": reason}
    recover = AsyncMock(return_value={"state": "waiting_email", "reason": "reset requested"})
    monkeypatch.setattr(Adapter, "recover", recover)
    return value, recover


async def test_a_lock_that_a_reset_lifts_requests_the_reset(service, monkeypatch):
    value, recover = signing_in(service, monkeypatch, locked_page(AUTH0))
    assert await Adapter.authenticate(value, locked_page(AUTH0), "sign_in") == {
        "state": "waiting_email", "reason": "reset requested"
    }
    recover.assert_awaited_once()
    assert value.accounts.record(URL)["state"] == "locked"


@pytest.mark.parametrize("error,reset", [
    ("Your account is locked. Contact the recruiting team.", True),
    (AUTH0, False),  # no reset control on the page
])
async def test_other_locks_still_park(service, monkeypatch, error, reset):
    value, recover = signing_in(service, monkeypatch, locked_page(error, reset=reset))
    result = await Adapter.authenticate(value, locked_page(error, reset=reset), "sign_in")
    assert result["reason"].startswith("The employer locked the account after failed sign-ins")
    recover.assert_not_awaited()


def test_the_unlock_wording():
    assert RESET_UNLOCKS.search(AUTH0)
    assert RESET_UNLOCKS.search("You can unlock your account by resetting your password.")
    assert not RESET_UNLOCKS.search("Too many attempts. Try again in 30 minutes.")
