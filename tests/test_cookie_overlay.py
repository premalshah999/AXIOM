"""Observed TI cookie panel plus consent and control-ownership boundaries."""

import pytest
from types import SimpleNamespace

from jobpilot.privacy import dismiss_cookies


@pytest.mark.parametrize("authorized", [True, False])
async def test_observed_cookie_anchor_releases_apply_only_with_saved_consent(page, authorized):
    await page.set_content("""<button id=apply onclick="window.opened=true">Apply Now</button>
      <div id=__tealiumGDPRecModal style="position:fixed;inset:0;background:white">
        <div class=privacy_prompt>TI websites use cookies to optimize site functionality.
          <a id=consent_prompt_submit href="javascript:void(0)"
             onclick="if(event.isTrusted)document.querySelector('#__tealiumGDPRecModal').remove()">AGREE AND PROCEED</a>
        </div></div>""")
    assert await dismiss_cookies(page, authorized=authorized) is authorized
    assert await page.locator("#__tealiumGDPRecModal").count() == (0 if authorized else 1)
    assert not await page.evaluate("!!window.opened")
    if authorized:
        await page.locator("#apply").click()
        assert await page.evaluate("window.opened")


@pytest.mark.parametrize(
    "content",
    [
        '<div class=consent>I agree that I meet the qualifications. <button onclick="window.sent=true">Agree</button></div>',
        '<div class=cookie>Cookies <button disabled onclick="window.sent=true">Accept</button></div>',
        '<div class=cookie>Cookies <a href="https://example.test/privacy" onclick="window.sent=true">Agree</a></div>',
        '<div class=cookie>Cookies <button style="display:none" onclick="window.sent=true">Accept</button></div>',
    ],
)
async def test_cookie_cleanup_does_not_click_application_or_unusable_controls(page, content):
    await page.set_content(content)
    assert not await dismiss_cookies(page, authorized=True)
    assert not await page.evaluate("!!window.sent")


async def test_workday_auth_uses_shared_native_cookie_action(page):
    from jobpilot.workday import WorkdayAuth
    from jobpilot.schemas import Profile

    await page.set_content("""<div class=cookie>Cookies
      <button onclick="window.accepted=event.isTrusted;this.parentElement.remove()">Accept</button></div>""")
    auth = WorkdayAuth.__new__(WorkdayAuth)
    auth.engine = SimpleNamespace(page=page, profile=Profile(accept_all_application_terms=True))
    assert await auth.dismiss_cookies()
    assert await page.evaluate("window.accepted")


SONI_BAR = """<button id=submit onclick="window.sent=true">Submit</button>
  <div id=bar style="position:fixed;bottom:0;left:0;right:0;height:60px;background:#222;color:white">
    This website uses analytics and tracking technologies to improve user experience.
    <button id=accept onclick="window.accepted=event.isTrusted;document.querySelector('#bar').remove()">Accept</button>
    <button id=decline onclick="window.declined=event.isTrusted;document.querySelector('#bar').remove()">Decline</button>
  </div>"""


async def test_a_tracking_bar_without_the_word_cookie_is_declined_when_nothing_is_saved(page):
    """Soni's bar covered the form's Submit on every run (custom site, 2026-10-31); declining gives no consent."""
    await page.set_content(SONI_BAR)
    assert await dismiss_cookies(page, authorized=False) is True
    assert await page.evaluate("window.declined") and not await page.evaluate("!!window.accepted")
    assert await page.locator("#bar").count() == 0


async def test_the_same_bar_is_accepted_with_the_saved_consent(page):
    await page.set_content(SONI_BAR)
    assert await dismiss_cookies(page, authorized=True) is True
    assert await page.evaluate("window.accepted") and not await page.evaluate("!!window.declined")


async def test_a_decline_button_outside_a_tracking_notice_is_never_clicked(page):
    await page.set_content(
        '<div style="position:fixed;bottom:0">Do you accept the offer? '
        '<button onclick="window.sent=true">Decline</button></div>'
        '<div>Analytics report <button onclick="window.sent=true">Decline</button></div>'
    )
    assert await dismiss_cookies(page, authorized=False) is False
    assert not await page.evaluate("!!window.sent")
