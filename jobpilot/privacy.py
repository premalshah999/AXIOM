"""Shared browser privacy actions, authorized by the saved applicant policy."""

import asyncio
import re

from playwright.async_api import Error as PlaywrightError

from .captcha_observation import PROVIDER_FRAME_JS


COOKIE_JS = r"""() => {
  const visible = e => e?.getClientRects().length && getComputedStyle(e).visibility !== 'hidden';
  const usable = e => visible(e) && !e.disabled && e.getAttribute('aria-disabled') !== 'true' && !e.closest('[inert]');
  const labels = /^(accept( all)?( cookies)?|allow( all)?( cookies)?|i accept|agree(?: and proceed)?|got it|ok)$/i;
  const selectors = ['#onetrust-accept-btn-handler','#truste-consent-button',
    '#CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll','.cc-allow','.cookie-accept',
    '[data-testid="cookie-accept"]','button[id*="accept" i][id*="cookie" i]',
    'button[aria-label*="accept" i][aria-label*="cookie" i]'];
  const roots = [...document.querySelectorAll(
    '[id*=cookie i],[class*=cookie i],[aria-label*=cookie i],#onetrust-banner-sdk,'+
    '#__tealiumGDPRecModal,.privacy_prompt,[id*=consent i],[class*=consent i]'
  )].filter(e => visible(e) && /\bcookies?\b/i.test(e.innerText || e.getAttribute('aria-label') || ''));
  // Dayforce (NCMEC 2026-10-16): a plain Ant Design card titled "Cookie Preferences" with Accept/Reject, no
  // cookie id or class, covered the Next button on every run. A small notice that starts with cookies counts.
  roots.push(...[...document.querySelectorAll(
    '[role=dialog],[role=alertdialog],[aria-modal=true],.ant-card,.ant-modal-content,aside,section'
  )].filter(e => visible(e) && (e.innerText || '').length < 1500 &&
    /^\s*(?:cookie (?:preferences|settings|consent|notice|policy)|we use (?:technology, such as )?cookies)\b/i
      .test(e.innerText || '')));
  const candidates = [...new Set([
    ...selectors.flatMap(s => [...document.querySelectorAll(s)]),
    ...roots.flatMap(root => [...root.querySelectorAll('button,a,[role=button]')]
      .filter(e => labels.test((e.innerText || e.getAttribute('aria-label') || '').trim())))
  ])].filter(usable);
  for (const e of candidates) {
    // Never activate a consent link that leaves the application, or an
    // obscured control. The site's native handler must dismiss its own notice.
    const href = e.getAttribute('href');
    if (href && !/^(?:#|javascript:void\(0\);?)$/i.test(href)) continue;
    const r=e.getBoundingClientRect(), hit=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);
    if (!(hit===e || e.contains(hit))) continue;
    e.dataset.jpCookieAction = e.dataset.jpCookieAction || ('cookie-' + Math.random().toString(36).slice(2));
    return e.dataset.jpCookieAction;
  }
  return false;
}"""


async def dismiss_cookies(page, *, authorized):
    """One bounded native cookie action per frame; no application consent guesses."""
    if not authorized:
        return False
    clicked = False
    for frame in page.frames[:12]:
        try:
            async with asyncio.timeout(2):
                target = await frame.evaluate(COOKIE_JS)
                if target:
                    control = frame.locator(f'[data-jp-cookie-action="{target}"]')
                    # Consent managers may ignore synthetic DOM click events.
                    # Playwright sends the real pointer action and keeps its
                    # visibility, enabled and hit-target checks.
                    await control.click(timeout=1500)
                    clicked = True
                    await control.wait_for(state="hidden", timeout=1500)
        except (PlaywrightError, TimeoutError):
            continue
    return clicked


# SuccessFactors account pages: "Data Privacy Acknowledgement *" is a link that opens the employer's privacy notice;
# its Acknowledge button stores the accepted statement's id in a hidden field the server requires.
PRIVACY_NOTICE_LINK = "#dataPrivacyId"
PRIVACY_NOTICE_RECEIPT = "#fbclc_dpcsId"
PRIVACY_NOTICE_ACTION = re.compile(r"^\s*(?:acknowledge|accept|i agree|agree)\s*$", re.I)


async def acknowledge_privacy_notice(page, *, authorized):
    """Open and acknowledge a required SuccessFactors privacy notice whose receipt is still empty.

    NetApp, Entergy, L3Harris and Farmers (2026-10-21): Create Account answered "Please open the data privacy
    agreement to accept the terms" until the notice was opened and acknowledged, and 30 jobs ended "Could not sign
    in". The link checks the country first, so this runs after the identity fields are filled.
    """
    if not authorized or page is None:
        return False
    for frame in page.frames[:12]:
        try:
            link, receipt = frame.locator(PRIVACY_NOTICE_LINK), frame.locator(PRIVACY_NOTICE_RECEIPT)
            if not await link.count() or not await receipt.count() or await receipt.first.input_value():
                continue
            await link.first.click(timeout=5000)
            dialog = frame.locator(".fd-dialog--active, [role=dialog]:visible")
            await dialog.locator("button").filter(has_text=PRIVACY_NOTICE_ACTION).first.click(timeout=8000)
            await frame.wait_for_function(
                "s => !!document.querySelector(s)?.value", arg=PRIVACY_NOTICE_RECEIPT, timeout=8000
            )
            return True
        except PlaywrightError:
            return False
    return False


LOCATION_DENIED = """(() => {
  if(""" + PROVIDER_FRAME_JS + """)return;
  const denied = (_, failure) => { if (failure) queueMicrotask(() => failure({
    code:1, message:'Location sharing is disabled by the applicant', PERMISSION_DENIED:1
  })); return 0; };
  if(navigator.geolocation) {
    navigator.geolocation.getCurrentPosition=denied;
    navigator.geolocation.watchPosition=denied;
    navigator.geolocation.clearWatch=()=>{};
  }
})()"""


async def deny_location(context, page):
    session = await context.new_cdp_session(page)
    try:
        # Omitting origin denies this permission in the entire dedicated context.
        await session.send(
            "Browser.setPermission",
            {
                "permission": {"name": "geolocation"},
                "setting": "denied",
            },
        )
    finally:
        await session.detach()
    await page.add_init_script(LOCATION_DENIED)
    for frame in page.frames:
        try:
            await frame.evaluate(LOCATION_DENIED)
        except Exception:
            pass
