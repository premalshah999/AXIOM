"""Read-only visibility of provider frames; never acts on a CAPTCHA."""

import re
from urllib.parse import urlsplit
from playwright.async_api import Error as PlaywrightError

# JS: true inside a challenge provider's own frame. Scripts the engine injects into every frame return early
# there: with the token hook inside hCaptcha's enclave, Lever's execute() made no request at all, while with the
# hook in the page only a puzzle loaded at once (REA Chrome, 2026-10-18). Form scanning skips these frames too.
PROVIDER_FRAME_JS = (
    r"(/(^|\.)(hcaptcha\.com|captcha-delivery\.com|arkoselabs\.com|funcaptcha\.com|geetest\.com|perimeterx\.net"
    r"|px-cdn\.net|px-cloud\.net)$/.test(location.hostname)||location.hostname==='challenges.cloudflare.com'"
    r"||/(^|\.)(google\.com|recaptcha\.net)$/.test(location.hostname)&&location.pathname.startsWith('/recaptcha'))"
)
ARM_JS = PROVIDER_FRAME_JS + "||(window.__jpArmed=true)"
DISARM_JS = PROVIDER_FRAME_JS + "||(window.__jpArmed=false)"


async def challenge_frame(page):
    for frame in page.frames:
        if frame == page.main_frame:
            continue
        try:
            owner = await frame.frame_element()
            identity = await owner.evaluate("e=>[e.title,e.src?.slice(0,250)].join(' ')")
            if not isinstance(identity, str):
                continue
            # Secure enclaves cover the viewport while the real challenge may be parked offscreen.
            # Some attached Chrome versions omit their same-origin child frames from page.frames.
            if "hcaptcha-enclave.html" in identity:
                host = frame.locator('iframe[title="hCaptcha challenge"]')
                if await host.count() == 1:
                    box = await host.bounding_box()
                    view = await page.evaluate("({width:innerWidth,height:innerHeight})")
                    if (
                        isinstance(view, dict)
                        and box
                        and box["width"] >= 250
                        and box["height"] >= 150
                        and (
                            box["x"] + box["width"] > 0
                            and box["y"] + box["height"] > 0
                            and box["x"] < view["width"]
                            and box["y"] < view["height"]
                        )
                    ):
                        owner = await host.element_handle()
                        child = await owner.content_frame()
                        if child or await host.evaluate("e=>!!e.contentDocument"):
                            return child or frame, owner, "hcaptcha nested challenge"
                continue
            box = await owner.bounding_box()
            if not box or box["width"] < 250 or box["height"] < 150 or not await owner.is_visible():
                continue
            if re.search(r"hcaptcha|recaptcha|challenge", identity, re.I):
                # Providers retain old challenge frames parked at -9999px.
                # Playwright is_visible checks CSS visibility, not the viewport.
                view = await page.evaluate("({width:innerWidth,height:innerHeight})")
                if not isinstance(view, dict) or not (
                    box["x"] + box["width"] > 0
                    and box["y"] + box["height"] > 0
                    and box["x"] < view["width"]
                    and box["y"] < view["height"]
                ):
                    continue
                return frame, owner, identity
        except PlaywrightError:
            continue
    return None


async def checkbox_frame(page):
    """A visible hCaptcha "I am human" checkbox whose response is still empty."""
    for frame in page.frames:
        if frame == page.main_frame:
            continue
        try:
            owner = await frame.frame_element()
            # Attached Chrome can expose a live child with an empty frame URL.
            # Its observed iframe source still identifies the provider widget.
            source = frame.url or await owner.get_attribute("src") or ""
            host = (urlsplit(source).hostname or "").lower()
            if (
                not (host == "hcaptcha.com" or host.endswith(".hcaptcha.com"))
                or "frame=checkbox" not in source
            ):
                continue
            if not await owner.is_visible():
                continue
            parent = await owner.evaluate_handle("e=>e.ownerDocument")
            empty = await parent.evaluate(
                "d=>[...d.querySelectorAll('[name=\"h-captcha-response\"]')].every(e=>!e.value)"
            )
            if empty:
                return frame
        except PlaywrightError:
            continue
    return None
