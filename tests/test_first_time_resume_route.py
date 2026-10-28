"""Intuit's Avature application page (2026-10-26/27) reads "Already registered?" (Username, Password, Log in, Forgot
your password?) beside "First time here?" with only "Without Resume", "Copy & Paste" and "My Computer" (a resume
upload) under it. There is no create-account link, so a refused sign-in for an account never seen there went to a
password reset, which never arrives for an address with no account, on every run. The new candidate's route is taken
instead: the resume goes into that upload. Synthetic data only."""

import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from jobpilot.adapters.base import Adapter
from support_icims import adapter

URL = "https://careers-acme.icims.com/jobs/42/engineer/login"
LOGIN = {"email": "alex@example.test", "password": "Synthetic9!x"}
TEXT = "Already registered?\nUsername\nPassword\nLog in\nForgot your password?\nFirst time here?\nMy Computer"


def page(text=TEXT):
    return {
        "url": URL,
        "text": text,
        "headings": ["Already registered?", "First time here?"] if "First time" in text else ["Sign in"],
        "errors": [],
        "fields": [
            {"id": "user", "type": "text", "label": "Username", "required": True, "automation_id": ""},
            {"id": "pass", "type": "password", "label": "Password", "required": True, "automation_id": ""},
            {"id": "cv", "type": "file", "label": "Resume/CV (My Computer *)", "required": True, "automation_id": ""},
        ],
        "controls": [{"id": "login", "label": "Log in", "href": "", "aria_label": "", "automation_id": ""},
                     {"id": "forgot", "label": "Forgot your password?", "href": "", "aria_label": "", "automation_id": ""}],
    }


def sign_in(service, monkeypatch, obs, **record):
    value = adapter(service, obs)
    value.id = "custom"
    value.final = re.compile(r"^Submit Application$", re.I)
    if record:
        value.accounts.mark(URL, record.pop("state"), LOGIN, **record)
    upload = AsyncMock()
    locators = {f["id"]: SimpleNamespace(fill=AsyncMock(), blur=AsyncMock(), set_input_files=upload) for f in obs["fields"]}
    advanced = {"url": URL + "/personal-info", "text": "Personal info", "headings": [], "errors": [],
                "fields": [{"id": "first", "type": "text", "label": "First name", "required": True, "automation_id": ""}],
                "controls": []}
    scans = iter([obs, obs, advanced])
    value.e.form = SimpleNamespace(
        scan=AsyncMock(side_effect=lambda: next(scans, advanced)),
        frames={f["id"]: SimpleNamespace(url=URL) for f in obs["fields"]},
        locator=lambda key: locators[key],
        page=SimpleNamespace(url=URL),
        resume_fields=lambda: [f for f in obs["fields"] if f["type"] == "file"],
        resume_path=Path("/tmp/synthetic-resume.pdf"),
        wait_upload=AsyncMock(),
        uploaded=False,
        resolver=SimpleNamespace(resolve=AsyncMock(return_value=[])),  # the resume is not a sign-in answer
    )
    value.e.profile = SimpleNamespace(account_username="")
    value.auth_outcome = AsyncMock(return_value=("bad_credentials", "The username or password may be incorrect"))
    value.switch = AsyncMock(return_value=False)  # no create-account link on this page
    recover = AsyncMock(return_value={"state": "waiting_browser", "reason": "reset"})
    monkeypatch.setattr(Adapter, "recover", recover)
    return value, upload, recover


@pytest.mark.parametrize("record", [{}, {"state": "credentials_rejected"}])
async def test_a_refused_sign_in_takes_the_first_time_resume_route(service, monkeypatch, record):
    obs = page()
    value, upload, recover = sign_in(service, monkeypatch, obs, **record)
    if record:
        # An earlier confirmed reset for this never-seen account went unanswered.
        import time

        value.accounts.mark(URL, "reset_requested", LOGIN, reset_requested_at=time.time() - 1200,
                            reset_submitted_at="2026-10-27T00:00:00+00:00")
    assert await value.on_sign_in(obs) is None
    upload.assert_awaited_once_with("/tmp/synthetic-resume.pdf", timeout=15000)
    recover.assert_not_awaited()
    assert value.accounts.record(URL)["state"] == "no_account"
    assert value.e.form.uploaded


async def test_a_sign_in_page_without_a_new_candidate_route_still_recovers(service, monkeypatch):
    obs = page("Sign in\nUsername\nPassword\nLog in\nForgot your password?")
    value, upload, recover = sign_in(service, monkeypatch, obs)
    await value.on_sign_in(obs)
    upload.assert_not_awaited()
    recover.assert_awaited()


async def test_the_route_is_taken_once_per_run(service, monkeypatch):
    obs = page()
    value, upload, recover = sign_in(service, monkeypatch, obs)
    value.attempts["first_time"] = 1
    await value.on_sign_in(obs)
    upload.assert_not_awaited()
    recover.assert_awaited()


async def test_legacy_unused_create_flag_does_not_block_resume_first_route(service, monkeypatch):
    import time

    obs = page()
    value, upload, recover = sign_in(service, monkeypatch, obs)
    value.accounts.mark(URL, "reset_requested", LOGIN, reset_requested_at=time.time() - 1200,
                        reset_submitted_at="2026-10-27T00:00:00+00:00",
                        absent_create_at="2026-10-27T00:01:00+00:00")
    assert await value.on_sign_in(obs) is None
    upload.assert_awaited_once()
    value.switch.assert_not_awaited()
    recover.assert_not_awaited()


async def test_local_upload_panel_is_opened_before_attaching_resume(service, monkeypatch):
    obs = page()
    obs['controls'].append({'id': 'local', 'label': 'My Computer', 'aria_label': '', 'automation_id': '', 'href': ''})
    value, upload, recover = sign_in(service, monkeypatch, obs)
    original = value.e.form.locator
    opened = AsyncMock()
    value.e.form.locator = lambda key: SimpleNamespace(click=opened) if key == 'local' else original(key)
    value.e.form.scan = AsyncMock(return_value=obs)
    value.find_next = lambda _: None
    assert await value.first_time_route(obs, URL, LOGIN)
    opened.assert_awaited_once_with(timeout=4000)
    upload.assert_awaited_once()
