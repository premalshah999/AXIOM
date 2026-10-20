"""Synthetic iCIMS account observations; no live credentials or websites."""

from collections import Counter
from types import SimpleNamespace
from unittest.mock import AsyncMock

from jobpilot.accounts import Accounts
from jobpilot.adapters.icims import ICIMS
from jobpilot.schemas import Profile

LOGIN = "https://login.icims.com/u/login/password?state=synthetic"
SIGNUP = "https://login.icims.com/u/signup?state=synthetic"


def observation(url=SIGNUP, *, password=True, email=True, label="Continue"):
    fields = []
    if email:
        fields.append({"id": "email", "type": "email", "label": "Email", "required": True})
    if password:
        fields.append({"id": "password", "type": "password", "label": "Password", "required": True})
    return {
        "url": url,
        "fields": fields,
        "controls": [{"id": "continue", "label": label, "href": ""}],
        "errors": [],
        "headings": [],
        "text": "Create your account",
    }


def authenticated_observation(employer="acme"):
    return {
        "url": f"https://careers-{employer}.icims.com/jobs/42/apply",
        "fields": [],
        "controls": [{"id": "logout", "label": "Log out", "href": ""}],
        "errors": [],
        "text": "",
        "headings": [],
    }


def adapter(service, obs, *, employer="acme", frame_url=None):
    value = ICIMS.__new__(ICIMS)
    page = SimpleNamespace(url=obs["url"])
    locators = {f["id"]: SimpleNamespace(fill=AsyncMock(), blur=AsyncMock()) for f in obs["fields"]}
    form = SimpleNamespace(
        scan=AsyncMock(return_value=authenticated_observation(employer)),
        frames={f["id"]: SimpleNamespace(url=frame_url or obs["url"]) for f in obs["fields"]},
        locator=lambda key: locators[key],
    )
    form.page = page
    value.e = SimpleNamespace(form=form, emit=lambda *args: None)
    value.service, value.config = service, service.config
    value.job = {"id": employer, "url": f"https://careers-{employer}.icims.com/jobs/42", "ats": "icims"}
    value.accounts = Accounts(
        service, Profile(email="alex@example.test", allow_account_creation=True), value.job["url"]
    )
    value.attempts = Counter()
    value.click = AsyncMock()
    value.arm_mail = AsyncMock(return_value=True)
    value.after_auth_click = AsyncMock(return_value=None)
    value.auth_outcome = AsyncMock(return_value=("ok", ""))
    value.release_mail = lambda: None
    value.reset_pending = lambda: False
    value.locators = locators
    return value
