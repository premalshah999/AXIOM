"""A reset is not the default recovery (2026-10-31).

Since 10-17, 17 of 221 password-reset challenges verified (8%), and 4 of 115 on accounts the employer itself said existed.
Textron asked nine times, Lenovo eight, Bain and Intuit seven. The first move for an account nothing shows is its sign-up
route (it answers 'already exists' if it does); after two confirmed resets nothing answered the job stops with its reason
and the employer is not asked again; a replacement address nobody registered is never sent a reset. Synthetic data only."""

import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from jobpilot.adapters.base import (
    ALIAS_UNREGISTERED,
    PASSWORD_UNKNOWN,
    RESET_UNANSWERED_CAP,
    Adapter,
    account_evidenced,
    resets_unanswered,
)
from jobpilot.email_alias import employer_alias_key
from support_icims import adapter, observation

URL = "https://careers-acme.icims.com/jobs/42/engineer/login"
LOGIN = {"email": "alex@example.test", "password": "Synthetic9!x"}


def stamp(seconds_ago=0):
    return (datetime.now(UTC) - timedelta(seconds=seconds_ago)).isoformat()


def recovering(service, state="credentials_rejected", **record):
    obs = observation(URL, email=False)
    obs["controls"] = [{"id": "f0", "label": "Forgot your password?", "aria_label": "", "automation_id": "", "type": "link"},
                       *obs["controls"]]
    value = adapter(service, obs)
    value.id = "custom"
    value.accounts.mark(URL, state, LOGIN, **record)
    value.auth_url = lambda _obs: URL
    value.emit = lambda *a, **k: None
    value.defer = lambda reason, reviews=None, **kw: {"reason": reason, **kw}
    value.inbox = SimpleNamespace(configured=True, rule=AsyncMock(return_value={"id": "rule"}), live=lambda c: False)
    value.mail_job = lambda kind: value.job
    value.earlier_reset_mail = AsyncMock(return_value=None)
    value.form.scan = AsyncMock(return_value=obs)
    value.switch = AsyncMock(return_value=True)
    value.form_changed = AsyncMock(return_value=True)
    value.open_reset_step = AsyncMock(return_value=obs)
    value.request_reset = AsyncMock(return_value={"state": "waiting_email"})
    value.e.run_id = "run1"
    value.config = SimpleNamespace(account_password_reset=True)
    return value, obs


@pytest.mark.parametrize(
    "record,evidenced",
    [
        ({}, False),
        ({"state": "credentials_rejected"}, False),  # a refused sign-in shows nothing
        ({"state": "reset_requested", "reset_submitted_at": "x"}, False),  # nor does a requested reset
        ({"state": "created_locally"}, False),
        ({"state": "authenticated"}, True),
        ({"state": "exists"}, True),
        ({"state": "credentials_rejected", "confirmed_at": "x"}, True),
        ({"state": "credentials_rejected", "identity_exists_at": "x"}, True),
    ],
)
def test_what_counts_as_the_employer_showing_an_account(record, evidenced):
    assert account_evidenced(record) is evidenced


def test_an_old_silence_is_not_held_against_the_employer():
    assert resets_unanswered({"resets_unanswered": 2, "resets_unanswered_at": stamp(3600)}) == 2
    assert resets_unanswered({"resets_unanswered": 2, "resets_unanswered_at": stamp(8 * 24 * 3600)}) == 0
    assert resets_unanswered({"resets_unanswered": 2}) == 0 and resets_unanswered({}) == 0


async def test_nothing_shows_an_account_so_the_sign_up_route_comes_before_a_reset(service):
    value, obs = recovering(service)
    assert await Adapter.recover(value, obs, "Invalid email or password") is None
    value.switch.assert_awaited_once()
    value.request_reset.assert_not_awaited()
    assert value.accounts.record(URL)["state"] == "no_account"
    # Once per run: when the page offers no sign-up route, the reset follows.
    value.switch = AsyncMock(return_value=False)
    value.accounts.mark(URL, "credentials_rejected", LOGIN)
    await Adapter.recover(value, obs, "Invalid email or password")
    value.request_reset.assert_awaited_once()


async def test_an_account_the_employer_showed_is_reset_not_created(service):
    value, obs = recovering(service, state="exists")
    await Adapter.recover(value, obs, "Invalid email or password")
    value.switch.assert_not_awaited()
    value.request_reset.assert_awaited_once()


async def test_after_two_unanswered_resets_the_job_stops_with_its_reason_and_asks_no_more(service):
    value, obs = recovering(service, state="exists", resets_unanswered=RESET_UNANSWERED_CAP, resets_unanswered_at=stamp(600))
    result = await Adapter.recover(value, obs, "Invalid email or password")
    assert result["reason"] == PASSWORD_UNKNOWN and result["state"] == "waiting_browser" and result["blocker_type"] == "account"
    value.request_reset.assert_not_awaited()
    # The same count a week later is forgotten: a silence of a sleeping Mac is not a verdict.
    value, obs = recovering(service, state="exists", resets_unanswered=RESET_UNANSWERED_CAP, resets_unanswered_at=stamp(8 * 24 * 3600))
    await Adapter.recover(value, obs, "Invalid email or password")
    value.request_reset.assert_awaited_once()


def with_alias(service, value):
    value.service = service
    local = "alex"
    value.accounts.email = f"{local}+apply@example.test"
    service.db.set_setting(employer_alias_key(value.job["url"]), {"tag": "apply"})


async def test_a_replacement_address_nobody_registered_is_never_sent_a_reset(service):
    value, obs = recovering(service)
    with_alias(service, value)
    value.switch = AsyncMock(return_value=False)  # the page offers no sign-up route
    result = await Adapter.recover(value, obs, "Invalid email or password")
    assert result["reason"] == ALIAS_UNREGISTERED and result["state"] == "waiting_browser"
    value.request_reset.assert_not_awaited()


async def test_a_replacement_address_that_signed_up_is_reset_like_any_account(service):
    value, obs = recovering(service, state="authenticated")
    with_alias(service, value)
    await Adapter.recover(value, obs, "Invalid email or password")
    value.request_reset.assert_awaited_once()


CENTRAL = "https://login.icims.com/u/login/password?state=tx1"


async def test_central_signs_a_replacement_address_up_instead_of_resetting_it(service):
    """National Louis (2026-10-30): Central showed its password step for the new address and the run reset an account
    nobody had created. The sign-up page of the same transaction is the route; without it the job stops, no reset."""
    value, obs = recovering(service)
    with_alias(service, value)
    value.job["url"] = "https://careers-acme.icims.com/jobs/42"
    value.accounts.email = "alex+apply@example.test"
    service.db.set_setting(employer_alias_key(value.job["url"]), {"tag": "apply"})
    value.e.form.page.url = CENTRAL
    value.accounts.record = lambda url: {}
    value.reset_pending = lambda: False
    value.on_create_account = AsyncMock(return_value={"state": "creating"})

    async def goto(url, **kw):
        value.e.form.page.url = url

    value.e.form.page.goto = AsyncMock(side_effect=goto)
    value.config = SimpleNamespace(account_password_reset=True)
    result = await value.try_central_signup(obs)
    assert result == {"state": "creating"}
    assert value.e.form.page.url == "https://login.icims.com/u/signup?state=tx1"
    value.request_reset.assert_not_awaited()
    # A transaction that offers no sign-up page ends in a clear stop, never a reset.
    value.e.form.page.url = CENTRAL
    value.e.form.page.goto = AsyncMock()
    value.on_create_account.reset_mock()
    stopped = await value.try_central_signup(obs)
    assert stopped["reason"] == ALIAS_UNREGISTERED and stopped["state"] == "waiting_browser"
    value.on_create_account.assert_not_awaited()
    value.request_reset.assert_not_awaited()


async def test_a_sign_up_link_that_opens_nothing_goes_to_the_reset_not_another_refused_login(service):
    value, obs = recovering(service)
    value.form_changed = AsyncMock(return_value=False)
    assert await Adapter.recover(value, obs, "Invalid email or password") == {"state": "waiting_email"}
    value.request_reset.assert_awaited_once()
    assert value.accounts.record(URL)["state"] != "no_account"


async def test_a_slow_sign_up_link_is_waited_for_and_an_unchanged_form_is_reported():
    login = {"fields": [{"label": "Password", "type": "password"}], "controls": []}
    signup = {"fields": [{"label": "Choose password", "type": "password"}], "controls": []}
    value = Adapter.__new__(Adapter)
    value.e = SimpleNamespace(form=SimpleNamespace(scan=AsyncMock(side_effect=[login, RuntimeError("navigating"), signup])))
    assert await Adapter.form_changed(value, login, wait=5)
    value.e.form.scan = AsyncMock(return_value=login)
    assert not await Adapter.form_changed(value, login, wait=0)


async def test_a_sign_up_page_with_no_way_back_returns_to_the_sign_in_page_for_its_reset(service):
    """Register and Cancel only: the "already exists" refusal stopped the run with "no password reset was offered"."""
    value, login = recovering(service, state="exists")
    registration = {**login, "controls": [{"id": "r", "label": "Register", "aria_label": "", "automation_id": "", "type": "button"}]}
    value.form.scan = AsyncMock(side_effect=[registration, login, login, login])
    value.switch = AsyncMock(return_value=False)
    value.sign_in_page = "https://careers-acme.icims.com/login?lang=en"
    goto = AsyncMock()
    value.e.page = value.form.page = SimpleNamespace(goto=goto, url=value.sign_in_page)
    assert await Adapter.recover(value, registration, "already exists") == {"state": "waiting_email"}
    assert goto.await_args.args[0] == value.sign_in_page
    value.request_reset.assert_awaited_once()
