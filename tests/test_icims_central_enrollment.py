"""Central (Auth0) offers passkey enrollment after a sign-in (Rovisys 2026-10-19): "Continue", "Remind me later" and
"Not on this device", all type=submit. The guided agent refused the declines as possible application submits, pressed
Continue, the device could not register, and it then refused "No Thanks" on the error page. The adapter declines."""

from unittest.mock import AsyncMock

from jobpilot.adapters.base import Step
from support_icims import adapter


def prompt(path, labels, host="login.icims.com"):
    return {
        "url": f"https://{host}{path}?state=synthetic",
        "fields": [],
        "controls": [{"id": f"c{i}", "label": label, "href": "", "type": "submit"} for i, label in enumerate(labels)],
        "errors": [],
        "headings": [],
        "text": "Log in faster on this device",
    }


ENROLL = prompt("/u/mfa-webauthn-platform-enrollment", ["Continue", "Remind me later", "Not on this device"])
ERROR = prompt("/u/mfa-webauthn-platform-error-enrollment", ["Try again", "No Thanks"])


async def test_the_enrollment_prompt_is_declined(service):
    value = adapter(service, ENROLL)
    value.form.settle = AsyncMock()
    assert value.classify(ENROLL) == Step.CONSENT
    assert await value.on_consent(ENROLL) is None
    assert value.click.await_args.args[0]["label"] == "Remind me later"
    assert value.click.await_args.kwargs["auth"] is True


async def test_the_registration_error_page_is_declined(service):
    value = adapter(service, ERROR)
    value.form.settle = AsyncMock()
    assert value.classify(ERROR) == Step.CONSENT
    await value.on_consent(ERROR)
    assert value.click.await_args.args[0]["label"] == "No Thanks"


async def test_a_prompt_that_keeps_returning_stops(service):
    value = adapter(service, ENROLL)
    value.form.settle = AsyncMock()
    for _ in range(3):
        assert await value.on_consent(ENROLL) is None
    result = await value.on_consent(ENROLL)
    assert result["state"] == "waiting_browser" and value.click.await_count == 3


def test_only_central_enrollment_pages_qualify(service):
    value = adapter(service, ENROLL)
    assert value.central_enrollment(prompt("/u/login/password", ["Continue", "Remind me later"])) is None
    assert value.central_enrollment(prompt("/u/mfa-webauthn-platform-enrollment", ["Remind me later"], host="evil.example")) is None
    assert value.central_enrollment(prompt("/u/passkey-enrollment-local", ["Continue", "Skip"]))["label"] == "Skip"
    assert value.central_enrollment(prompt("/u/mfa-webauthn-platform-enrollment", ["Continue"])) is None


async def test_the_guided_flow_lets_the_adapter_decline_the_prompt(service):
    value = adapter(service, ENROLL)
    value.form.settle = AsyncMock()
    assert await value.guided_step(ENROLL) is True
    assert value.click.await_args.args[0]["label"] == "Remind me later"
    other = prompt("/u/login/password", ["Continue"])
    assert await adapter(service, other).guided_step(other) is False


def test_the_two_step_verification_prompt_is_skipped(service):
    """Planview, 2026-10-22: "You haven't enabled two-step verification" on login-page.icims.com went to the guided
    agent, which clicked the site's Careers link until the run stalled."""
    value = adapter(service, prompt("/u/login/password", ["Continue"]))
    labels = ["Back", "Enable two-step verification", "No, just a password", "Skip for now"]
    page = prompt("/ui/login-continue", labels, host="login-page.icims.com")
    assert value.central_enrollment(page)["label"] == "Skip for now"
    assert value.central_enrollment(prompt("/ui/login-continue", labels, host="careers-acme.icims.com")) is None
