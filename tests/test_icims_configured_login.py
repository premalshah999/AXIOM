import re
from unittest.mock import AsyncMock

import pytest

from jobpilot.accounts import Accounts
from jobpilot.config import Settings
from jobpilot.schemas import Profile
from support_icims import LOGIN, adapter, observation


def test_observed_job_apply_is_distinct_from_returning_user_login(service):
    obs = observation(
        "https://careers-acme.icims.com/jobs/42/software-engineer/job", password=False, email=False
    )
    returning = {"id": "returning", "label": "Log back in!", "href": "/jobs/42/software-engineer/login"}
    apply = {
        "id": "apply",
        "label": "Apply for this job online",
        "href": "/jobs/42/software-engineer/job?mode=apply&apply=yes",
    }
    obs["controls"] = [returning, apply]
    value = adapter(service, obs)
    assert value.find_apply(obs) is apply
    obs["controls"] = [returning]
    assert value.find_apply(obs) is None


def test_schwabs_apply_for_this_job_enters_the_jobs_own_login_step(service):
    # Schwab 2026-10-21: "Apply for this job" (no "online") led to /jobs/<id>/<slug>/login and was never recognized.
    obs = observation(
        "https://career-schwab.icims.com/jobs/127949/associate-software-engineer/job?in_iframe=1",
        password=False,
        email=False,
    )
    login = {"id": "login", "label": "Log in", "href": "https://career-schwab.icims.com/jobs/login?loginOnly=1"}
    apply = {"id": "apply", "label": "Apply for this job",
             "href": "https://career-schwab.icims.com/jobs/127949/associate-software-engineer/login"}
    other = {"id": "other", "label": "Apply for this job",
             "href": "https://career-schwab.icims.com/jobs/999/other-role/login"}
    obs["controls"] = [login, other, apply]
    assert adapter(service, obs).find_apply(obs) is apply


def test_icims_login_env_is_loaded_without_appearing_in_settings_repr(monkeypatch):
    monkeypatch.setenv("ICIMS_LOGIN", "SyntheticWorking9!")
    config = Settings(_env_file=None)
    assert config.icims_login == "SyntheticWorking9!"
    assert "SyntheticWorking9!" not in repr(config)


@pytest.mark.parametrize(
    "url", ["https://login.icims.com/u/login/password", "https://careers-example.icims.com/login"]
)
def test_working_icims_password_precedes_stale_saved_password_without_replacing_it(service, url):
    service.config.icims_login = "SyntheticWorking9!"
    accounts = Accounts(
        service, Profile(email="alex@example.test"), "https://careers-example.icims.com/jobs/42"
    )
    accounts.mark(url, "credentials_rejected", {"email": accounts.email, "password": "OldSynthetic8!"})
    before = accounts.record(url)
    assert accounts.credentials(url) == {"email": accounts.email, "password": "SyntheticWorking9!"}
    assert accounts.record(url) == before
    assert accounts.alternative_credentials(url) is None


@pytest.mark.parametrize(
    "url",
    [
        "https://employer.example.test/login",
        "https://icims.com.evil.test/login",
        "http://login.icims.com/login",
        "https://user@login.icims.com/login",
        "https://login.icims.com:444/login",
    ],
)
def test_icims_password_never_leaves_trusted_https_icims_hosts(service, url):
    service.config.icims_login = "SyntheticWorking9!"
    accounts = Accounts(
        service, Profile(email="alex@example.test"), "https://careers-example.icims.com/jobs/42"
    )
    assert accounts.icims_credentials(url) is None


async def test_explicit_working_login_reaches_password_authentication_without_local_account_record(service):
    service.config.icims_login = "SyntheticWorking9!"
    obs = observation(LOGIN, email=False)
    obs["controls"][0].update(aria_label="", automation_id="")
    value = adapter(service, obs)
    value.final = re.compile(r"^Submit Application$", re.I)
    value.try_central_signup = AsyncMock()
    assert not value.accounts.record(LOGIN)
    assert await value.on_sign_in(obs) is None
    value.locators["password"].fill.assert_awaited_once_with("SyntheticWorking9!", timeout=4000)
    value.try_central_signup.assert_not_awaited()
    assert value.accounts.record(LOGIN)["state"] == "authenticated"
    assert value.accounts.saved(LOGIN)["password"] == "SyntheticWorking9!"


async def test_working_login_rejection_does_not_claim_account_or_request_reset(service):
    # Without the owner's reset authorization (account_password_reset off) a rejection stays a rejection.
    service.config.account_password_reset = False
    service.config.icims_login = "SyntheticWorking9!"
    obs = observation(LOGIN, email=False)
    obs["controls"][0].update(aria_label="", automation_id="")
    value = adapter(service, obs)
    value.final = re.compile(r"^Submit Application$", re.I)
    value.auth_outcome = AsyncMock(return_value=("bad_credentials", "Invalid password"))
    value.form.scan = AsyncMock(return_value=obs)
    value.request_reset = AsyncMock()
    result = await value.on_sign_in(obs)
    assert result["blocker_type"] == "account"
    assert value.accounts.record(LOGIN)["state"] == "credentials_rejected"
    value.request_reset.assert_not_awaited()
    value.click.assert_awaited_once()
    # Another continuation must respect rejection of this same credential version.
    value.attempts.clear()
    value.click.reset_mock()
    assert (await value.on_sign_in(obs))["blocker_type"] == "account"
    value.click.assert_not_awaited()


async def test_owner_authorized_reset_replaces_a_rejected_central_password(service, monkeypatch):
    """Owner 2026-10-17: "set whatever password you want with iCIMS" -> a repeated rejection resets it."""
    from jobpilot.adapters.base import Adapter

    service.config.account_password_reset = True
    service.config.icims_login = "SyntheticWorking9!"
    obs = observation(LOGIN, email=False)
    obs["controls"][0].update(aria_label="", automation_id="")
    value = adapter(service, obs)
    value.final = re.compile(r"^Submit Application$", re.I)
    value.auth_outcome = AsyncMock(return_value=("bad_credentials", "Invalid password"))
    value.form.scan = AsyncMock(return_value=obs)
    recover = AsyncMock(return_value={"state": "waiting_browser", "blocker_type": "email_wait"})
    monkeypatch.setattr(Adapter, "recover", recover)
    await value.on_sign_in(obs)
    recover.assert_awaited()
    # A later continuation with the same rejected password goes to the reset, never another blind sign-in.
    value.attempts.clear()
    value.click.reset_mock()
    await value.on_sign_in(obs)
    assert recover.await_count >= 2
    value.click.assert_not_awaited()


def test_the_email_step_is_known_by_its_protocol_name_and_submit_id(service):
    # Schwab 2026-10-21: email plus an optional phone, submitted by "I Acknowledge the Privacy Notice".
    from jobpilot.adapters.base import Step

    url = "https://career-schwab.icims.com/jobs/128575/full-stack-developer/login"
    obs = observation(url, password=False, email=False)
    obs["fields"] = [
        {"id": "e", "type": "email", "label": "Email", "required": False, "value": "", "icims_name": "css_loginName",
         "frame_url": url + "?in_iframe=1", "automation_id": ""},
        {"id": "c", "type": "combobox", "label": "Phone Country Code", "required": False, "value": "", "options": [],
         "icims_name": "", "frame_url": url + "?in_iframe=1", "automation_id": ""},
        {"id": "n", "type": "text", "label": "Number", "required": False, "value": "", "icims_name": "css_phoneNumber",
         "frame_url": url + "?in_iframe=1", "automation_id": ""},
    ]
    submit = {"id": "s", "label": "I Acknowledge the Privacy Notice", "href": "", "html_id": "enterEmailSubmitButton",
              "automation_id": "", "aria_label": ""}
    obs["controls"] = [{"id": "x", "label": "Search jobs", "href": "", "html_id": "js-search-toggle",
                        "automation_id": "", "aria_label": ""}, submit]
    value = adapter(service, obs)
    assert value.classify(obs) == Step.EMAIL_ENTRY
    assert value.email_submit(obs) is submit


@pytest.mark.parametrize("step", ["form?form=Employment_Application&isPacket=1", "questions?global=1", "questions", "eeo"])
@pytest.mark.parametrize("framed", [False, True])
def test_the_forms_packet_is_an_application_step_by_its_path(service, step, framed):
    """Prime, run f9754403943d439d85501c8065ffae5d, 2026-10-21: packet has no progress caption."""
    from jobpilot.adapters.base import Step

    url = "https://careers-primeinc.icims.com/jobs/5263/junior-rpgle-developer/" + step
    obs = observation("https://careers-primeinc.icims.com/jobs/5263/job" if framed else url,
                      password=False, email=False)
    obs["fields"] = [{"id": "q", "type": "select", "label": "Are you at least 18?", "required": True, "value": "",
                      "options": ["Yes", "No"], "frame_url": url, "automation_id": "", "icims_name": "rcf1"}]
    obs["controls"] = [{"id": "n", "label": "Submit", "href": "", "automation_id": "", "aria_label": ""}]
    assert adapter(service, obs).classify(obs) == Step.FORM
