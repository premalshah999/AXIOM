"""The engine's init scripts never run inside a challenge provider's own frames (REA Chrome, 2026-10-18).

page.add_init_script reaches every frame. With the token HOOK inside hCaptcha's enclave frames, Lever's own
hcaptcha.execute() made no request at all (no getcaptcha, no puzzle, no token: Lever never confirmed, and the engine
bought solver-IP tokens Lever refused). With the hook in the page only, the same click fetched a puzzle at once.
The submit GUARD, the geolocation override and commit arming stay out of those frames for the same reason.
"""

import pytest
import pytest_asyncio

from jobpilot.browser import GUARD
from jobpilot.captcha import HOOK
from jobpilot.captcha_observation import ARM_JS
from jobpilot.privacy import LOCATION_DENIED

FRAMES = {
    "https://newassets.hcaptcha.com/captcha/v1/abc/static/hcaptcha-enclave.html": True,
    "https://geo.captcha-delivery.com/captcha/?initialCid=x": True,
    "https://www.google.com/recaptcha/api2/anchor?k=x": True,
    "https://challenges.cloudflare.com/cdn-cgi/challenge-platform/turnstile/x": True,
    "https://boards.greenhouse.io/embed/job_app?for=x": False,
}
STATE = """() => ({hook: !!window.__jpTokenCaptcha, armed: window.__jpArmed === true,
  geolocation: navigator.geolocation.getCurrentPosition.toString().includes('[native code]')})"""


@pytest_asyncio.fixture(loop_scope="session")
async def framed(page):
    async def fulfill(route):
        await route.fulfill(body="<!doctype html><title>frame</title><p>frame</p>", content_type="text/html")

    for url in FRAMES:
        await page.route(url.split("?")[0] + "*", fulfill)
    await page.route("https://jobs.lever.co/**", lambda route: route.fulfill(
        body="<!doctype html><body>" + "".join(f'<iframe src="{url}"></iframe>' for url in FRAMES) + "</body>",
        content_type="text/html"))
    for script in (GUARD, LOCATION_DENIED, HOOK):
        await page.add_init_script(script)
    await page.goto("https://jobs.lever.co/acme/123/apply")
    await page.wait_for_function(f"document.querySelectorAll('iframe').length === {len(FRAMES)}")
    await page.wait_for_timeout(300)
    return page


async def test_injected_scripts_skip_provider_frames(framed):
    for frame in framed.frames:
        await frame.evaluate(ARM_JS)
    states = {frame.url: await frame.evaluate(STATE) for frame in framed.frames}
    assert states["https://jobs.lever.co/acme/123/apply"] == {"hook": True, "armed": True, "geolocation": False}
    for url, provider in FRAMES.items():
        if provider:
            assert states[url] == {"hook": False, "armed": False, "geolocation": True}, url
        else:
            assert states[url] == {"hook": True, "armed": True, "geolocation": False}, url
