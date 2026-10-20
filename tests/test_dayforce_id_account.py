"""Dayforce ID (dfid.dayforcehcm.com) accounts, Imperial Brady and Security Benefit 2026-10-16..18.

Every run pressed "Next" on Create Your Account, saw the form stay (its invisible reCAPTCHA gave no token, and the
page's own messages sit outside the scanner's error selectors), recorded the account as created, then followed
"Have an account? Sign In" to sign in to an account that never existed. No run reached the security questions.
These cover: page recognition (one /account/login path serves registration and sign-in), the outcome of Next and its
routing (no token, "Registration failed", the existing-account notice), Sign In without an account, the multifactor
opt-out, and the guest application a tenant's sitecontext allows.
"""

from types import SimpleNamespace

import pytest

from jobpilot import accounts
from jobpilot.accounts import AccountFlow, dayforce_id_outcome, dayforce_id_page, dayforce_id_password_ok
from jobpilot.answers import Resolver
from jobpilot.forms import FormSession
from jobpilot.schemas import Profile

DFID = "https://dfid.dayforcehcm.com/globalidentity"
REGISTER = DFID + "/account/login?returnUrl=%2Fconnect%2Fauthorize%2Fcallback%3FclientNamespace%3Dacme%26action%3Dregister"
JOB = "https://jobs.dayforcehcm.com/en-US/acme/CANDIDATEPORTAL/jobs/1"


def test_pages_are_told_apart_by_title_heading_and_return_url():
    assert dayforce_id_page({"url": REGISTER}) == "register"
    assert dayforce_id_page({"url": REGISTER.replace("register", "login")}) == "sign_in"
    assert dayforce_id_page({"url": DFID + "/account/login"}) == "sign_in"
    assert dayforce_id_page({"url": DFID + "/account/login", "title": "Create Your Account | Dayforce Identity"}) == "register"
    assert dayforce_id_page({"url": DFID + "/account/register/securityquestions"}) == "security_questions"
    assert dayforce_id_page({"url": DFID + "/users/verification", "headings": ["Enter Verification Code"]}) == "verification"
    assert dayforce_id_page({"url": DFID + "/account/register/mfasetup"}) == "mfa"
    assert dayforce_id_page({"url": JOB + "/apply", "title": "Sign In"}) == ""


def test_password_rules_and_generated_passwords():
    assert not dayforce_id_password_ok("Abcdefg1")  # no special character
    assert not dayforce_id_password_ok("Abcdef=1")  # "=" is not on Dayforce's list
    assert not dayforce_id_password_ok("AAAb!xyz")  # a character three times in a row
    assert dayforce_id_password_ok("Abcdef!1")
    assert all(dayforce_id_password_ok(accounts.AccountStore.generated(16)) for _ in range(500))


def test_next_outcomes_are_named():
    failed = {"validation": ["Registration failed. Try again using a different email address and/or password."]}
    assert dayforce_id_outcome("register", "security_questions", {}) == "advanced"
    assert dayforce_id_outcome("register", "register", failed) == "registration_failed"
    assert dayforce_id_outcome("register", "register", {"modal": ["It looks like you have an existing account"]}) == "existing_account"
    assert dayforce_id_outcome("register", "register", {"toasts": ["Your request can't be completed right now."]}) == "request_failed"
    assert dayforce_id_outcome("register", "register", {"captcha": [{"pending": True}]}) == "captcha"
    assert dayforce_id_outcome("register", "register", {"captcha": [{"pending": False}]}) == "no_response"
    assert dayforce_id_outcome("security_questions", "verification", {}) == "advanced"


def registration(next_action):
    return f"""<!doctype html><html><body><h1>Create Your Account</h1><form onsubmit="return false">
<label for="firstName">First Name</label><input id="firstName" required>
<label for="lastName">Last Name</label><input id="lastName" required>
<label for="emailAddress">Email Address (Required)</label><input id="emailAddress" required autocomplete="email">
<label for="confirmEmailAddress">Confirm Email Address</label><input id="confirmEmailAddress" required>
<label for="password">Password</label><input id="password" type="password" required autocomplete="new-password">
<label for="confirmPassword">Confirm Password</label><input id="confirmPassword" type="password" required>
<label><input type="checkbox" id="agree"> I agree to Acme's Global Privacy Statement and to the Terms of Use.</label>
<p class="validation-text-module__evrValidationText__x" id="vt"></p>
<button type="button" id="next" onclick="{next_action}">Next</button>
<p>Have an account? <a href="#signin" onclick="window.signin = true; return false">Sign In</a></p>
</form></body></html>"""


NOTHING = "window.pressed = (window.pressed || 0) + 1"
FAILED = (
    "document.getElementById('vt').textContent = "
    "'Registration failed. Try again using a different email address and/or password.'"
)
NOTICE = (
    "document.body.insertAdjacentHTML('beforeend', '<div id=modal_existingAccountMigration role=dialog aria-modal=true "
    "style=width:420px><h2>It looks like you have an existing account</h2><p>We found your previous account on our "
    "older career site.</p><button type=button onclick=window.continued=true>Continue</button></div>')"
)


async def nothing(*_a, **_k):
    return None


@pytest.fixture
def flow_for(page, service, tmp_path, monkeypatch):
    monkeypatch.setattr(accounts, "DAYFORCE_ID_OUTCOME_SECONDS", 1)

    async def make(url, body):
        await page.route(url, lambda route: route.fulfill(content_type="text/html", body=body))
        await page.goto(url)
        resume = tmp_path / "r.pdf"
        resume.write_bytes(b"%PDF")
        profile = Profile(
            first_name="Alex", last_name="Rivera", email="alex@example.test", allow_account_creation=True,
            accept_all_application_terms=True,
        )
        form = FormSession(page, Resolver(profile, service.db, service.config), resume, lambda *a, **k: None)
        events = []
        engine = SimpleNamespace(
            service=service, form=form, page=page, job={"url": JOB}, result=None,
            emit=lambda *a, **k: events.append(a), email_verification=SimpleNamespace(prepare_submission=nothing),
            browser_challenge=nothing,
        )
        return AccountFlow(engine), events

    return make


async def test_next_without_a_recaptcha_token_is_reported_and_never_turns_into_sign_in(page, flow_for):
    flow, events = await flow_for(REGISTER, registration(NOTHING))
    assert await flow.handle()
    assert await page.evaluate("window.pressed") == 1
    assert flow.e.result["blocker_type"] == "captcha"
    assert flow.store.state(REGISTER) == "registering"  # never created_locally or authenticated
    outcome = next(data for kind, _, *data in events if kind == "account_outcome")
    assert outcome[0]["outcome"] == "no_response"
    flow.e.result = None
    await flow.handle()  # the form is still there on the next pass
    assert await page.evaluate("window.signin") is None
    assert flow.store.state(REGISTER) == "registering"


async def test_registration_failed_records_the_account_and_signs_in_instead(page, flow_for):
    flow, _ = await flow_for(REGISTER, registration(FAILED))
    assert await flow.handle()
    assert flow.store.state(REGISTER) == "exists"
    assert await page.evaluate("window.signin") is True
    assert flow.e.result is None


async def test_existing_account_notice_continues_with_its_only_action(page, flow_for):
    flow, events = await flow_for(REGISTER, registration(NOTICE))
    assert await flow.handle()
    assert await page.evaluate("window.continued") is True
    assert await page.evaluate("window.signin") is None
    assert any("existing-account notice" in str(message) for _, message, *_ in events)


SIGN_IN = """<!doctype html><html><head><title>Sign In | Dayforce Identity</title></head><body><h1>Sign In</h1>
<form onsubmit="return false">
<label for="email">Email</label><input id="email" type="text">
<label for="password">Password</label><input id="password" type="password">
<a href="#forgot">Forgot Password?</a>
<button type="button" onclick="window.signedin = true">Sign In</button>
<p>Don't have an account? <a href="#register" onclick="window.create = true; return false">Create one now.</a></p>
</form></body></html>"""


async def test_sign_in_without_a_created_account_opens_create_one_now(page, flow_for):
    url = DFID + "/account/login"
    flow, _ = await flow_for(url, SIGN_IN)
    flow.store.save(url, flow.store.shared(), "signing_in")  # the production record left by 10-16
    assert await flow.handle()
    assert await page.evaluate("window.create") is True
    assert await page.evaluate("window.signedin") is None


MFA = """<!doctype html><html><head><title>Multifactor Authentication | Dayforce Identity</title></head><body>
<h1>Multifactor Authentication</h1><span id="mfaLabel">Select your multifactor authentication method</span>
<form id="mfaForm" onsubmit="return false"><div role="radiogroup" id="mfaOptions" aria-labelledby="mfaLabel">
<label id="emailOption"><input type="radio" name="mfaOptions" checked> Email</label>
<label id="textMessageOption"><input type="radio" name="mfaOptions"> Text Message</label>
<label id="phoneCallOption"><input type="radio" name="mfaOptions"> Phone Call</label>
<label id="optOutOption"><input type="radio" name="mfaOptions"> I do not want to use multifactor authentication</label>
</div><button type="button" onclick="window.saved = document.querySelector('#optOutOption input').checked">Save</button>
</form></body></html>"""


async def test_multifactor_setup_takes_the_opt_out(page, flow_for):
    url = DFID + "/account/register/mfasetup"
    flow, _ = await flow_for(url, MFA)
    flow.store.save(url, flow.store.shared(), "registering")
    assert await flow.handle()
    assert await page.evaluate("window.saved") is True
    assert flow.store.state(url) == "verification_pending"


def flow_selection(allowed):
    data = '{"props":{"pageProps":{"dehydratedState":{"enableApplyWithoutAccount":%s}}}}' % str(allowed).lower()
    late = (
        "<script>setTimeout(() => document.body.insertAdjacentHTML('afterbegin', '<button type=button "
        "onclick=window.guest=true>Apply without an Account</button>'), 700)</script>"
        if allowed
        else ""
    )
    return f"""<!doctype html><html><body><script id="__NEXT_DATA__" type="application/json">{data}</script>
<h2>Already Have an Account?</h2><button type="button">Sign In</button>
<p>Don't have an account? <a href="#">Create one now.</a></p>{late}</body></html>"""


async def test_guest_tenant_waits_for_its_apply_without_an_account(page, flow_for):
    flow, _ = await flow_for(JOB + "/apply?flowSelection=true", flow_selection(True))
    assert await flow.apply_as_guest(await flow.e.form.scan())
    assert await page.evaluate("window.guest") is True


async def test_account_only_tenant_does_not_wait_for_a_guest_button(page, flow_for):
    flow, _ = await flow_for(JOB + "/apply?flowSelection=true", flow_selection(False))
    assert not await flow.apply_as_guest(await flow.e.form.scan())
