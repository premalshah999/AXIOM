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
# The employer's page confirmed the reset request (request_reset), so its silence means something.
CONFIRMED = "2026-10-22T00:00:00+00:00"


@pytest.mark.parametrize("minutes_ago,stops", [(20, True), (2, False)])
async def test_an_unanswered_reset_marks_the_password_unknown(service, minutes_ago, stops):
    obs = observation(URL)
    value = adapter(service, obs)
    value.accounts.mark(URL, "reset_requested", {"email": "alex@example.test", "password": "Synthetic9!x"},
                        reset_requested_at=time.time() - minutes_ago * 60, reset_submitted_at=CONFIRMED)
    value.defer = lambda reason, *a, **k: {"state": k.get("state", "waiting_answer"), "reason": reason}
    value.config.account_password_reset = False  # the ordinary path ends at its first guard
    result = await Adapter.recover(value, obs, "Signing in did not succeed")
    assert (result["reason"] == PASSWORD_UNKNOWN) is stops
    assert (value.accounts.record(URL)["state"] == "password_unknown") is stops


@pytest.mark.parametrize("hours_ago,waits", [(1, True), (7, False)])
async def test_a_password_unknown_account_waits_then_resets_again(service, hours_ago, waits):
    """Owner 2026-10-27: resets through the mailbox are always allowed; a recent unanswered one waits, an old one is
    requested again, and the autopilot keeps retrying the job."""
    from datetime import UTC, datetime, timedelta

    obs = observation(URL)
    value = adapter(service, obs)
    since = (datetime.now(UTC) - timedelta(hours=hours_ago)).isoformat()
    value.accounts.mark(URL, "password_unknown", {"email": "alex@example.test", "password": "Synthetic9!x"},
                        reset_submitted_at=CONFIRMED, password_unknown_at=since)
    value.defer = lambda reason, *a, **k: {"state": k.get("state"), "reason": reason}
    value.config.account_password_reset = True
    from types import SimpleNamespace

    value.inbox = SimpleNamespace(configured=False)  # the reset path ends at its mailbox guard here
    result = await Adapter.recover(value, obs, "Signing in did not succeed")
    assert (result == {"state": "waiting_browser", "reason": PASSWORD_UNKNOWN}) is waits
    assert not PERSIST_SKIP.search(PASSWORD_UNKNOWN)


@pytest.mark.parametrize("ats", ["successfactors", "avature", "taleo", "custom"])
async def test_an_unanswered_reset_creates_the_account_once(service, monkeypatch, ats):
    """SuccessFactors says a reset that sends nothing means "no matching account was found". L3Harris (2026-10-21..23)
    requested four resets that never came: the saved login was from a registration that never finished. One Create is
    tried; the state stays settled so no further reset is requested. Avature (Lenovo, MetLife, Bain), Taleo (Textron)
    and custom sites say the same "if an account exists" and reset on every rerun (2026-10-28)."""
    import re
    from unittest.mock import AsyncMock

    obs = observation(URL, email=False)
    obs["controls"][0].update(aria_label="", automation_id="")
    value = adapter(service, obs)
    value.id = ats
    value.job["ats"] = ats
    value.final = re.compile(r"^Submit Application$", re.I)
    value.accounts.mark(URL, "password_unknown", {"email": "alex@example.test", "password": "Synthetic9!x"},
                        reset_submitted_at=CONFIRMED)
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


@pytest.mark.parametrize("message,unregistered", [
    ("Incorrect email", True),
    ("Invalid email address", True),
    ("Incorrect email or password", False),
    ("Invalid email address or password.", False),
    ("Incorrect password", False),
])
def test_an_unregistered_address_reads_as_no_account(message, unregistered):
    """TikTok's careers login answers an unregistered address with "Incorrect email"; runs kept signing in (2026-10-26)."""
    from jobpilot.adapters.base import NOT_FOUND

    assert bool(NOT_FOUND.search(message)) is unregistered


def test_avatures_sign_in_wording_reads_as_refused_and_create_profile_as_sign_up():
    """Deloitte's Avature login says "The username or password may be incorrect, or access might be restricted." and
    offers new candidates "Create profile!"; runs read the refusal as unknown and never registered (2026-10-26)."""
    from jobpilot.adapters.base import BAD_CREDENTIALS, CREATE_LINK

    assert BAD_CREDENTIALS.search("The username or password may be incorrect, or access might be restricted.")
    assert CREATE_LINK.fullmatch("Create profile!") and CREATE_LINK.fullmatch("Create account")
    assert not CREATE_LINK.fullmatch("Create profile and apply later today")


@pytest.mark.parametrize("job_host,frame_host,allowed", [
    ("deloitteus.avature.net", "apply.deloitte.com", True),     # Deloitte's Avature tenant on its own domain
    ("acme.wd5.myworkdayjobs.com", "careers.acme.com", True),
    ("careers-acme.icims.com", "jobs.acme.com", True),
    ("deloitteus.avature.net", "apply.deloitte-careers-login.example", False),
    ("acmecorp.avature.net", "acm.com", False),                 # too short a name to identify the employer
    ("deloitteus.avature.net", "evil.com", False),
])
def test_an_ats_tenant_names_its_employers_own_domain(job_host, frame_host, allowed):
    """Deloitte's Avature tenant signs candidates in on apply.deloitte.com; the password was refused as "an unexpected
    website" and the run never registered (2026-10-26)."""
    from jobpilot.accounts.credentials import employer_domain_of_tenant

    assert employer_domain_of_tenant(job_host, frame_host) is allowed


@pytest.mark.parametrize("text,kind", [
    ("We can't find an account with that email address.", "not_found"),
    ("No account found for this email.", "not_found"),
    ("This email is not registered.", "not_found"),
    ("Your account may be locked. Please try again in 30 minutes.", "locked"),
    ("Too many sign-in attempts.", "locked"),
    ("Login failed. Please check your details.", "bad"),
    ("We couldn't sign you in.", "bad"),
    ("The email or password you entered is incorrect.", "bad"),
    ("Incorrect email or password", "bad"),
])
def test_common_sign_in_wording_is_understood(text, kind):
    """Owner 2026-10-26: sign-in and sign-up texts on known and unknown platforms were missed."""
    from jobpilot.adapters.base import BAD_CREDENTIALS, LOCKED, NOT_FOUND

    found = {"not_found": bool(NOT_FOUND.search(text)), "locked": bool(LOCKED.search(text)),
             "bad": bool(BAD_CREDENTIALS.search(text))}
    assert found[kind]
    if kind == "bad":
        assert not NOT_FOUND.search(text)  # A refused password is never read as "no account".


async def test_a_failed_new_password_step_requests_no_further_reset_on_this_build(service, monkeypatch):
    # L3Harris (2026-10-27): every run requested another reset and the owner got reset links "all the time".
    from unittest.mock import AsyncMock

    from jobpilot.runtime import engine_version

    obs = observation(URL)
    value = adapter(service, obs)
    value.accounts.mark(URL, "credentials_rejected", {"email": "alex@example.test", "password": "Synthetic9!x"},
                        reset_failed_engine=engine_version())
    value.defer = lambda reason, *a, **k: {"state": k.get("state"), "reason": reason}
    value.config.account_password_reset = True
    value.inbox = type("Inbox", (), {"configured": True, "rule": AsyncMock(return_value={"id": "r"})})()
    value.request_reset = AsyncMock()
    result = await Adapter.recover(value, obs, "Signing in did not succeed")
    assert "no further reset emails" in result["reason"]
    value.request_reset.assert_not_awaited()


def test_a_locked_account_is_never_retried_by_the_autopilot():
    # By Light (iCIMS 2026-10-27) was retried twice after the employer locked the account.
    assert PERSIST_SKIP.search("The employer locked the account after failed sign-ins: account has been blocked")
