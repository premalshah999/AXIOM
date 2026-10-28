"""Live! Casino & Hotel Maryland (iCIMS Central, 2026-10-26..27): after a good sign-in, login-page.icims.com/ui/
login-continue said "You haven't enabled two-step verification" with only Back, Enable two-step verification (an
authenticator app or SMS) and Log Out; Back answered "Two-factor authentication is required to access this
application". Read as unknown, the guided agent logged out, re-entered the email step and requested a password reset on
every retry. The wall ends the run with its own reason, which the autopilot does not retry. Planview's optional prompt
(Skip for now) is still declined."""

from unittest.mock import AsyncMock

from jobpilot.adapters.base import Step
from jobpilot.autopilot import PERSIST_SKIP
from support_icims import adapter


def page(url, labels, heading="", text=""):
    return {
        "url": url,
        "fields": [],
        "controls": [{"id": f"c{i}", "label": label, "href": ""} for i, label in enumerate(labels)],
        "errors": [],
        "headings": [heading] if heading else [],
        "text": f"{heading} {text}".strip(),
    }


CONTINUE = "https://login-page.icims.com/ui/login-continue"
PROMPT = "You haven't enabled two-step verification"
MANDATORY = page(CONTINUE, ["Back", "Enable two-step verification", "Log Out"], PROMPT)
OPTIONAL = page(CONTINUE, ["Enable two-step verification", "No, just a password", "Skip for now"], PROMPT)
WENT_WRONG = page(
    "https://login.icims.com/authorize/resume?state=synthetic", [],
    text="Something Went Wrong: Two-factor authentication is required to access this application. To enable this, "
         "please contact your system administrator.",
)


async def test_the_mandatory_prompt_ends_the_run(service):
    value = adapter(service, MANDATORY)
    assert value.classify(MANDATORY) == Step.CONSENT
    result = await value.on_consent(MANDATORY)
    value.click.assert_not_awaited()
    assert result["state"] == "waiting_browser" and "two-step verification" in result["reason"]
    assert PERSIST_SKIP.search(result["reason"])


async def test_the_required_error_page_ends_the_run_too(service):
    value = adapter(service, WENT_WRONG)
    assert value.classify(WENT_WRONG) == Step.CONSENT
    assert (await value.on_consent(WENT_WRONG))["state"] == "waiting_browser"


async def test_the_guided_flow_stops_at_the_wall(service):
    value = adapter(service, MANDATORY)
    assert value.site_lock(MANDATORY)["state"] == "waiting_browser"
    assert value.site_lock(OPTIONAL) is None


async def test_an_optional_prompt_is_still_skipped(service):
    value = adapter(service, OPTIONAL)
    value.form.settle = AsyncMock()
    assert value.classify(OPTIONAL) == Step.CONSENT
    assert await value.on_consent(OPTIONAL) is None
    assert value.click.await_args.args[0]["label"] == "Skip for now"


def test_only_central_hosts_qualify(service):
    elsewhere = {**MANDATORY, "url": "https://careers-acme.icims.com/jobs/42/job"}
    assert not adapter(service, elsewhere).central_mfa_required(elsewhere)
