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


# Textron (Taleo), Lenovo (Avature), Publicis (iCIMS Central EU) and Simplify (2026-10-28/29): up to six confirmed resets
# per job in a day, none delivered. A refused sign-in overwrote "reset_requested" with "credentials_rejected", so the
# unanswered reset was never seen as unanswered; the account's stamp from an earlier false "authenticated" kept the
# create-once gate shut; and Taleo's "Create a New Account" did not match the registration link pattern.
STAMP = "2026-10-24T16:17:43+00:00"


@pytest.mark.parametrize("label,matches", [
    ("Create a New Account", True),
    ("Create your account", True),
    ("Create New Account", True),
    ("Create profile!", True),
    ("Create alert", False),
    ("Create a new job alert", False),
    ("Create profile and apply later today", False),
])
def test_the_registration_link_reads_a_new_account_wording(label, matches):
    from jobpilot.adapters.base import CREATE_LINK

    assert bool(CREATE_LINK.fullmatch(label)) is matches


async def test_an_unanswered_reset_of_a_stamped_account_creates_it_once_and_stays_settled(service, monkeypatch):
    value, obs = sign_in(service, monkeypatch, reset_requested_at=time.time() - 1200, reset_submitted_at=CONFIRMED,
                         confirmed_at=STAMP)
    assert await value.on_sign_in(obs) is None
    value.switch.assert_awaited()
    record = value.accounts.record(URL)
    assert record["state"] == "password_unknown" and record.get("absent_create_at") and record.get("password_unknown_at")


async def test_a_refusal_after_an_unanswered_reset_does_not_request_another_reset(service, monkeypatch):
    """Without a registration link the settled state, not "credentials_rejected", reaches recover(): no new reset."""
    value, obs = sign_in(service, monkeypatch, reset_requested_at=time.time() - 1200, reset_submitted_at=CONFIRMED,
                         confirmed_at=STAMP)
    monkeypatch.undo()
    monkeypatch.setattr(Adapter, "recover", Adapter.recover)
    value.switch = AsyncMock(return_value=False)
    value.first_time_route = AsyncMock(return_value=False)
    value.request_reset = AsyncMock()
    emitted = []
    value.emit = lambda kind, message, data=None: emitted.append((kind, message, data))
    value.defer = lambda reason, *a, **k: {"state": k.get("state"), "reason": reason}
    value.config.account_password_reset = True
    result = await value.on_sign_in(obs)
    value.request_reset.assert_not_awaited()
    assert result["reason"].startswith("This employer has an account")
    assert value.accounts.record(URL)["state"] == "password_unknown"
    # The page's own controls are named, so a wording the pattern misses shows in the trail.
    assert any("no registration link" in m and d and d.get("controls") for _, m, d in emitted)


async def test_a_recent_reset_still_awaiting_delivery_keeps_its_state(service, monkeypatch):
    value, obs = sign_in(service, monkeypatch, reset_requested_at=time.time() - 120, reset_submitted_at=CONFIRMED,
                         confirmed_at=STAMP)
    await value.on_sign_in(obs)
    assert value.accounts.record(URL)["state"] == "credentials_rejected"


def test_an_unanswered_reset_is_not_proof_that_the_account_exists(service):
    from jobpilot.accounts.store import AccountStore  # noqa: F401  (the store behind value.accounts)

    obs = observation(URL)
    value = adapter(service, obs)
    value.accounts.mark(URL, "password_unknown", LOGIN)
    assert not value.accounts.record(URL).get("confirmed_at")
    value.accounts.mark(URL, "authenticated", LOGIN)
    assert value.accounts.record(URL).get("confirmed_at")


@pytest.mark.parametrize("record,bounced", [
    # Simplify (2026-10-29): /auth/register opened for the one registration was turned back to sign-in at once.
    ({"state": "reset_requested", "reset_requested_at": time.time() - 1200, "reset_submitted_at": CONFIRMED}, False),
    # The same page for an account that is proven stays a sign-in.
    ({"state": "authenticated", "confirmed_at": STAMP}, True),
    # The reset is still awaiting delivery: nothing says the account is absent.
    ({"state": "reset_requested", "reset_requested_at": time.time() - 60, "reset_submitted_at": CONFIRMED}, True),
])
async def test_the_registration_after_an_unanswered_reset_is_not_turned_back_to_sign_in(service, record, bounced):
    obs = observation(URL)
    value = adapter(service, obs)
    value.accounts.mark(URL, record.pop("state"), LOGIN, **record)
    value.switch = AsyncMock(return_value=True)
    value.auth_button = lambda *a: None  # the form is not driven here; only the sign-in switch is under test
    result = await value.authenticate(obs, "create")
    assert (value.switch.await_count == 1 and result is None) is bounced
    if not bounced:
        assert value.accounts.record(URL).get("absent_create_at")
