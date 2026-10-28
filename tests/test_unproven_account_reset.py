"""Bain, Electronic Arts and Intuit (2026-10-26/27): their career sites answer a reset with "If the email address you
entered is associated with an account in our records, you will receive an email", and four to six confirmed resets
each brought nothing. Nothing had ever shown that an account existed there (no Create, sign-in or "already
registered"), so every run signed in, was refused, and requested another reset. After one unanswered confirmed reset,
such an account is created once. Synthetic data only."""

import re
import time
from unittest.mock import AsyncMock

import pytest

from jobpilot.adapters.base import Adapter
from support_icims import adapter, observation

URL = "https://careers-acme.icims.com/jobs/42/engineer/login"
CONFIRMED = "2026-10-27T00:00:00+00:00"
LOGIN = {"email": "alex@example.test", "password": "Synthetic9!x"}


def sign_in(service, monkeypatch, **record):
    obs = observation(URL, email=False)
    obs["controls"][0].update(aria_label="", automation_id="")
    value = adapter(service, obs)
    value.id = "custom"
    value.final = re.compile(r"^Submit Application$", re.I)
    value.accounts.mark(URL, record.pop("state", "reset_requested"), LOGIN, **record)
    value.auth_outcome = AsyncMock(return_value=("bad_credentials", "Invalid email address or password."))
    value.form.scan = AsyncMock(return_value=obs)
    value.switch = AsyncMock(return_value=True)
    monkeypatch.setattr(Adapter, "recover", AsyncMock(return_value={"state": "waiting_browser"}))
    return value, obs


async def test_an_unanswered_reset_for_an_account_never_seen_creates_it_once(service, monkeypatch):
    value, obs = sign_in(service, monkeypatch, reset_requested_at=time.time() - 1200, reset_submitted_at=CONFIRMED)
    assert await value.on_sign_in(obs) is None
    value.switch.assert_awaited()
    record = value.accounts.record(URL)
    assert record["state"] == "no_account" and record.get("absent_create_at")
    assert not record.get("confirmed_at")  # not an account: the sign-up page is not turned back to sign-in
    # Tried once: a later refusal never creates again.
    value.attempts.clear()
    value.switch.reset_mock()
    value.accounts.mark(URL, "reset_requested", LOGIN, reset_requested_at=time.time() - 1200)
    await value.on_sign_in(obs)
    value.switch.assert_not_awaited()


async def test_missing_registration_route_does_not_spend_creation_attempt(service, monkeypatch):
    value, obs = sign_in(service, monkeypatch, reset_requested_at=time.time() - 1200, reset_submitted_at=CONFIRMED)
    value.switch = AsyncMock(return_value=False)
    value.first_time_route = AsyncMock(return_value=False)
    await value.on_sign_in(obs)
    assert not value.accounts.record(URL).get("absent_create_at")


@pytest.mark.parametrize(
    "record",
    [
        {"reset_age": 120, "reset_submitted_at": CONFIRMED},  # still awaiting delivery
        {"reset_age": 1200},  # the page never confirmed the request
        {"state": "authenticated"},  # this app signed in here before: the account exists
    ],
)
async def test_a_reset_that_proves_nothing_keeps_the_reset_path(service, monkeypatch, record):
    record = dict(record)
    if "reset_age" in record:
        record["reset_requested_at"] = time.time() - record.pop("reset_age")
    value, obs = sign_in(service, monkeypatch, **record)
    await value.on_sign_in(obs)
    value.switch.assert_not_awaited()
