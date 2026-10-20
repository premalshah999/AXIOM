"""Dayforce (NCMEC 2026-10-16): the cookie notice is an Ant Design card titled "Cookie Preferences" with Accept and
Reject and no cookie id/class/aria-label. It covered the form's Next button, and every run failed on that click."""

from jobpilot.privacy import dismiss_cookies

PAGE = """<form><label for="start">If hired, when would you be available to start?</label><input id="start">
<button type="button" id="next" style="position:fixed;right:40px;bottom:40px">Next</button></form>
<div class="ant-card ant-card-bordered" id="card" style="position:fixed;right:0;bottom:0;width:420px;height:300px;background:#fff">
  <div class="ant-card-head"><div class="ant-card-head-title"><h2>Cookie Preferences</h2></div></div>
  <div class="ant-card-body"><p>We use technology, such as cookies, to improve site functionality.</p>
    <button type="button" class="ant-btn" onclick="card.remove()"><span>Accept</span></button>
    <button type="button" class="ant-btn"><span>Reject</span></button></div></div>"""


async def test_a_cookie_card_without_cookie_markup_is_dismissed(page):
    await page.set_content(PAGE)
    assert await dismiss_cookies(page, authorized=True)
    assert await page.locator("#card").count() == 0


async def test_an_application_card_mentioning_cookies_later_is_not_a_banner(page):
    await page.set_content("""<div class="ant-card" id="card"><h2>Bakery Assistant</h2>
      <p>Bake cookies and bread.</p><button type="button" onclick="card.dataset.clicked=1">Accept</button></div>""")
    assert not await dismiss_cookies(page, authorized=True)
    assert await page.locator("#card").get_attribute("data-clicked") is None
