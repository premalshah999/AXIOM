"""Dayforce ID registration (Security Benefit, Imperial Brady 2026-10-16): the form's only action is "Next".
The account flow accepted only Create/Register/Sign up/Continue, refused the action as ambiguous after typing
the passwords, and every registration stalled with "Password is required"."""

from types import SimpleNamespace

from jobpilot.accounts import AccountFlow
from jobpilot.answers import Resolver
from jobpilot.forms import FormSession
from jobpilot.schemas import Profile

URL = "https://dfid.dayforcehcm.com/globalidentity/account/login?returnUrl=%2Fconnect%2Fauthorize%3FclientNamespace%3Dacme%26action%3Dregister"
PAGE = """<!doctype html><html><body><form onsubmit="return false">
<label for="firstName">First Name</label><input id="firstName" required>
<label for="lastName">Last Name</label><input id="lastName" required>
<label for="emailAddress">Email Address (Required)</label><input id="emailAddress" required autocomplete="email">
<label for="confirmEmailAddress">Confirm Email Address</label><input id="confirmEmailAddress" required>
<label for="password">Password</label><input id="password" type="password" required autocomplete="new-password">
<label for="confirmPassword">Confirm Password</label><input id="confirmPassword" type="password" required>
<label><input type="checkbox" id="agree"> I agree to Acme's Global Privacy Statement and to the Terms of Use.</label>
<button type="button" id="next" onclick="window.sent = [password.value, confirmPassword.value, emailAddress.value]">Next</button>
<p>Have an account? <a href="#signin">Sign In</a></p>
</form></body></html>"""


async def test_registration_whose_only_action_is_next_sends_the_credentials(page, service, tmp_path):
    await page.route(URL, lambda route: route.fulfill(content_type="text/html", body=PAGE))
    await page.goto(URL)
    resume = tmp_path / "r.pdf"
    resume.write_bytes(b"%PDF")
    profile = Profile(
        first_name="Alex", last_name="Rivera", email="alex@example.test", allow_account_creation=True,
        accept_all_application_terms=True,
    )
    form = FormSession(page, Resolver(profile, service.db, service.config), resume, lambda *a: None)

    async def nothing(*_a, **_k):
        return None

    events = []
    engine = SimpleNamespace(
        service=service, form=form, page=page, job={"url": "https://jobs.dayforcehcm.com/en-US/acme/CANDIDATEPORTAL/jobs/1"},
        emit=lambda *a, **k: events.append(a[0]), email_verification=SimpleNamespace(prepare_submission=nothing),
        browser_challenge=nothing,
    )
    flow = AccountFlow(engine)
    assert await flow.handle()
    password, confirm, email = await page.evaluate("window.sent")
    assert password and password == confirm
    assert email == flow.store.shared()["email"]
    assert "account" in events
