"""Shared browser privacy actions, authorized by the saved applicant policy."""

import asyncio
import re

from playwright.async_api import Error as PlaywrightError

from .captcha_observation import PROVIDER_FRAME_JS


COOKIE_JS = r"""mode => {
  const visible = e => e?.getClientRects().length && getComputedStyle(e).visibility !== 'hidden';
  const usable = e => visible(e) && !e.disabled && e.getAttribute('aria-disabled') !== 'true' && !e.closest('[inert]');
  // Accepting needs the saved consent; declining tracking never gives one, so a banner over the page is always answerable.
  const accepting = mode !== 'decline';
  const labels = accepting
    ? /^(accept( all)?( cookies)?|allow( all)?( cookies)?|i accept|agree(?: and proceed)?|got it|ok)$/i
    : /^(decline( all)?( cookies)?|reject( all)?( cookies)?|deny( all)?|no,? thanks|(?:use |accept )?(?:only )?(?:strictly )?(?:necessary|essential)(?: cookies)?(?: only)?)$/i;
  const selectors = accepting ? ['#onetrust-accept-btn-handler','#truste-consent-button',
    '#CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll','.cc-allow','.cookie-accept',
    '[data-testid="cookie-accept"]','button[id*="accept" i][id*="cookie" i]',
    'button[aria-label*="accept" i][aria-label*="cookie" i]'] : [
    '#onetrust-reject-all-handler','#CybotCookiebotDialogBodyButtonDecline'];
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
  // A notice with no cookie id, class or word of its own: Soni's bar ("This website uses analytics and tracking
  // technologies", Accept / Decline) hid the final Submit of the form above it and every click was "covered". A control
  // that carries the label, inside a pinned bar that names tracking, is that notice.
  const NOTICE = /\b(?:cookies?|analytics|tracking technologies|tracking)\b/i;
  const pinnedNotice = e => {
    for (let n = e.parentElement, i = 0; n && n !== document.body && i < 5; n = n.parentElement, i++) {
      const pos = getComputedStyle(n).position, text = n.innerText || '';
      if ((pos === 'fixed' || pos === 'sticky') && text.length < 1500 && NOTICE.test(text)
          && n.getBoundingClientRect().height < innerHeight * 0.45) return true;
    }
    return false;
  };
  const pinned = [...document.querySelectorAll('button,a,[role=button]')]
    .filter(e => labels.test((e.innerText || e.getAttribute('aria-label') || '').trim()) && visible(e) && pinnedNotice(e));
  const candidates = [...new Set([
    ...pinned,
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
    """One bounded native cookie action per frame; no application consent guesses. With the saved consent it accepts;
    without it, it only declines (or keeps the necessary cookies), which gives no consent and uncovers the page."""
    mode = "accept" if authorized else "decline"
    clicked = False
    for frame in page.frames[:12]:
        try:
            async with asyncio.timeout(2):
                target = await frame.evaluate(COOKIE_JS, mode)
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
    return await acknowledge_gated_agreement(page)


# A required agreement box that stays disabled until its statement was opened and acknowledged: SAP Customer Data Cloud's
# registration ("Read and acknowledge the data privacy statement.", Atlas Copco, 2026-10-31/11-01) has no #dataPrivacyId
# link and no receipt field. The inert Submit that followed was counted as a registration and one run used up the day.
GATED_AGREEMENT_JS = """(() => {
  const marketing = /newsletter|notification|alert|opportunit|marketing|promot|offers|updates/i;
  for (const box of document.querySelectorAll('input[type=checkbox]')) {
    if (!box.disabled || box.checked || !box.offsetParent) continue;
    let node = box, host = null;
    for (let i = 0; i < 4 && node.parentElement; i++) {
      node = node.parentElement;
      if (node.querySelector('a, button, [role=button]')) { host = node; break; }
    }
    const text = ((host || box.parentElement || box).innerText || '').replace(/\\s+/g, ' ');
    if (!/privacy|terms|data protection|agreement/i.test(text) || (marketing.test(text) && !/privacy|terms/i.test(text))) continue;
    const opener = [...host.querySelectorAll('a, button, [role=button]')].find(
      x => x.offsetParent && /acknowledge|accept|read|privacy|terms|statement|agreement/i.test(x.innerText || x.textContent || ''));
    if (!opener) continue;
    box.setAttribute('data-jp-gated', '1');
    opener.setAttribute('data-jp-gated-opener', '1');
    return true;
  }
  return false;
})()"""


async def acknowledge_gated_agreement(page):
    """Open the statement a disabled agreement box waits for, acknowledge it, and report whether the box is now ticked."""
    for frame in page.frames[:12]:
        try:
            if not await frame.evaluate(GATED_AGREEMENT_JS):
                continue
            await frame.locator("[data-jp-gated-opener]").first.click(timeout=5000)
            dialog = frame.locator("[role=dialog]:visible, .fd-dialog--active, [class*=dialog]:visible, [class*=modal]:visible")
            action = dialog.locator("button, a, [role=button], input[type=button]").filter(has_text=PRIVACY_NOTICE_ACTION)
            await action.first.click(timeout=8000)
            box = frame.locator("[data-jp-gated]")
            await frame.wait_for_function(
                "() => { const b = document.querySelector('[data-jp-gated]'); return !!b && !b.disabled; }", timeout=8000
            )
            if not await box.first.is_checked():
                await box.first.check(timeout=4000)
            return await box.first.is_checked()
        except (PlaywrightError, TimeoutError):
            return False
        finally:
            try:
                await frame.evaluate(
                    "() => document.querySelectorAll('[data-jp-gated],[data-jp-gated-opener]').forEach("
                    "e => { e.removeAttribute('data-jp-gated'); e.removeAttribute('data-jp-gated-opener'); })"
                )
            except PlaywrightError:
                pass
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
