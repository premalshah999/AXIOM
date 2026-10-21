"""iCIMS Connect (Schwab 2026-10-17): the talent-network "Submit Profile" that find_apply follows as account
preparation is clicked as such, never refused as the application's final submit, and the run returns to the
posting's own Apply afterwards instead of reading the profile page as a receipt."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

from jobpilot.adapters.base import Adapter
from jobpilot.adapters.icims import ICIMS
from support_icims import adapter


def connect_profile(service, url):
    obs = {"url": url, "fields": [], "controls": [], "errors": [], "headings": [], "text": ""}
    value = adapter(service, obs, employer="schwab")
    del value.click  # the real ICIMS.click
    value.e.form.settle = AsyncMock()
    value.e.return_to_job = AsyncMock()
    return value


async def test_connect_submit_profile_is_account_preparation(service, monkeypatch):
    parent = AsyncMock(return_value=None)
    monkeypatch.setattr(Adapter, "click", parent)
    value = connect_profile(service, "https://careers-schwab.icims.com/connect/profile?parseResume=1")
    await ICIMS.click(value, {"id": "s", "label": "Submit Profile", "href": ""})
    assert parent.await_args.kwargs["auth"] is True
    value.e.return_to_job.assert_awaited_once()
    assert value.accounts.record(value.job["url"]).get("state") == "exists"


async def test_other_submits_stay_final(service, monkeypatch):
    parent = AsyncMock(return_value=None)
    monkeypatch.setattr(Adapter, "click", parent)
    # The job's own candidate profile creates the account (ICE 2026-10-19): an account action, never Connect's
    # return to the posting.
    application = connect_profile(service, "https://careers-schwab.icims.com/jobs/42/candidate?from=login")
    await ICIMS.click(application, {"id": "s", "label": "Submit Profile", "href": ""})
    assert parent.await_args.kwargs["auth"] is True
    application.e.return_to_job.assert_not_awaited()
    assert application.accounts.record(application.job["url"]).get("state") != "exists"
    await ICIMS.click(application, {"id": "s", "label": "Submit Application", "href": ""})
    assert parent.await_args.kwargs.get("auth", False) is False
    elsewhere = connect_profile(service, "https://careers-other.icims.com/jobs/42/candidate?from=login")
    await ICIMS.click(elsewhere, {"id": "s", "label": "Submit Profile", "href": ""})
    assert parent.await_args.kwargs.get("auth", False) is False
    other_host = connect_profile(service, "https://careers-other.icims.com/connect/profile")
    assert not ICIMS.connect_profile_submit(other_host, {"label": "Submit Profile", "href": ""})
    assert not ICIMS.connect_profile_submit(
        connect_profile(service, "https://careers-schwab.icims.com/connect/profile"), {"label": "Submit Application", "href": ""}
    )


def test_offered_connect_link_can_reach_owned_signup(service):
    from support_icims import observation
    obs = observation("https://careers-acme.icims.com/jobs/42", password=False, email=False)
    button = {"id":"connect", "label":"Connect with us", "href":"https://careers-acme.icims.com/connect"}
    obs["controls"] = [button]
    value = adapter(service, obs)
    assert value.offered_profile_signup(obs) == button
    button["href"] = "https://careers-other.icims.com/connect"
    assert value.offered_profile_signup(obs) is None
    button["href"] = "https://careers-acme.icims.com/talent-network"
    assert value.offered_profile_signup(obs) is None


def test_rejected_credentials_do_not_hide_offered_registration(service):
    from support_icims import observation
    obs=observation("https://careers-acme.icims.com/jobs/42",password=False,email=False)
    signup={"id":"connect","label":"Connect with us","href":"https://careers-acme.icims.com/connect"}
    obs["controls"]=[signup,{"id":"apply","label":"Apply for this job online","href":"/jobs/42/login"}]
    value=adapter(service,obs)
    value.accounts.mark(value.job["url"],"credentials_rejected")
    assert value.find_apply(obs)==signup
    value.accounts.mark(value.job["url"],"authenticated")
    assert value.find_apply(obs)["id"]=="apply"


def test_new_candidate_enters_job_instead_of_talent_network(service):
    """VTG, run d499dd84 (handoff prefix), 2026-10-21: no account must not select Connect."""
    from support_icims import observation

    obs = observation("https://careers-acme.icims.com/jobs/42/engineer/job", password=False, email=False)
    signup = {"id": "connect", "label": "Join our talent network",
              "href": "https://careers-acme.icims.com/connect/interests"}
    apply = {"id": "apply", "label": "Apply for this job", "href": "/jobs/42/engineer/login"}
    obs["controls"] = [signup, apply]
    value = adapter(service, obs)
    assert not value.accounts.record(value.job["url"])
    assert value.find_apply(obs) is apply
