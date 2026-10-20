"""Shared browser privacy actions, authorized by the saved applicant policy."""

import asyncio

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
