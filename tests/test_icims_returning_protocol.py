import pytest

from jobpilot.adapters.base import Step
from support_icims import LOGIN, adapter, observation


def returning_page(service):
    url = "https://careers-acme.icims.com/jobs/42/engineer/job"
    obs = observation(url, password=False, email=False)
    obs["fields"] = [{"id": "locale", "type": "select", "label": "url", "name": "url",
                      "html_id": "footer-language-selector", "required": False}]
    obs["controls"] = [
        {"id": "return", "label": "Log back in!", "href": "/jobs/42/engineer/login?loginOnly=1&redirect=App"},
        {"id": "apply", "label": "Apply for this job onlineApply", "href": url + "?mode=apply&apply=yes"},
    ]
    value = adapter(service, obs)
    return value, obs


def test_atlassian_locale_menu_does_not_hide_the_job_entry(service):
    """Atlassian, run f0cc54b43e3d4d58b3e4da34b3a8c546, 2026-10-21: footer URL select caused FORM."""
    value, obs = returning_page(service)
    assert value.classify(obs) == Step.JOB
    assert value.find_apply(obs)["id"] == "apply"
    value.accounts.mark(value.job["url"], "created_locally", {"email": "alex@example.test", "password": "SyntheticLocal9!"})
    assert value.find_apply(obs)["id"] == "return"
    obs["controls"][0]["href"] = "https://careers-other.icims.com/jobs/42/engineer/login?loginOnly=1"
    assert value.find_apply(obs)["id"] == "apply"


def test_vtg_apply_here_uses_the_same_job_apply_protocol(service):
    """VTG, run 153ca3d84d6645c487578111845d7430, 2026-10-21: Apply HereApply did not match labels."""
    value, obs = returning_page(service)
    obs["fields"] = []
    obs["controls"][1]["label"] = "Apply HereApply"
    assert value.find_apply(obs)["id"] == "apply"
    assert value.classify(obs) == Step.JOB
    obs["controls"][1]["href"] = "https://careers-other.icims.com/jobs/42/engineer/job?mode=apply&apply=yes"
    assert value.find_apply(obs) is None


def test_authenticated_job_frame_uses_apply_despite_wrapper_login(service):
    """Atlassian, run f0cc54b43e3d4d58b3e4da34b3a8c546, 2026-10-21: returning route ends after sign-in."""
    value, obs = returning_page(service)
    value.accounts.mark(value.job["url"], "authenticated")
    obs["controls"] += [
        {"id": "wrapper", "label": "Log in", "href": "/login", "frame_url": obs["url"]},
        {"id": "logout", "label": "Log Out", "href": "/logout", "frame_url": obs["url"] + "?in_iframe=1"},
    ]
    assert value.find_apply(obs)["id"] == "apply"


async def test_atlassian_new_employer_password_survives_returning_login(service):
    """Atlassian, run f0cc54b43e3d4d58b3e4da34b3a8c546, 2026-10-21: use saved per-employer password."""
    service.config.icims_login = "SyntheticDifferentTenant9!"
    obs = observation(LOGIN, email=False)
    obs["controls"][0].update(aria_label="", automation_id="")
    value = adapter(service, obs)
    saved = {"email": "alex@example.test", "password": "SyntheticLocal9!"}
    value.accounts.mark(value.job["url"], "created_locally", saved)
    await value.on_sign_in(obs)
    value.locators["password"].fill.assert_awaited_once_with(saved["password"], timeout=4000)
    assert value.accounts.saved(LOGIN) == saved
    assert value.accounts.record(LOGIN)["state"] == "authenticated"


@pytest.mark.parametrize("foreign", ["https://example.test/login", "https://careers-other.icims.com/jobs/42"])
def test_other_employer_has_no_access_to_saved_local_password(service, foreign):
    """Atlassian, run f0cc54b43e3d4d58b3e4da34b3a8c546, 2026-10-21: credentials stay tenant-scoped."""
    value, _ = returning_page(service)
    value.accounts.mark(value.job["url"], "created_locally", {"email": "alex@example.test", "password": "SyntheticLocal9!"})
    assert value.accounts.saved(foreign) is None
