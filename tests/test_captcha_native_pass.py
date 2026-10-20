"""Lever (hCaptcha Enterprise, secure-api.js), REA analysis 2026-10-18: Lever's page calls hcaptcha.execute() and
submits from its own callback. In a real browser the checkbox can pass without a puzzle; the same-IP solver saw no
image challenge and the caller then bought a token from the solver's IP and delivered it over the browser's own, which
Lever rejected (HTTP 400 "security check", 10 of 15 Lever runs). A token hCaptcha delivered natively is used as is."""

from unittest.mock import AsyncMock

from jobpilot.captcha import HOOK, CaptchaSolver

LEVER = """<form id=application-form method=POST enctype=multipart/form-data>
  <input type=hidden id=hcaptchaResponseInput name=h-captcha-response>
  <div id=h-captcha class=h-captcha data-sitekey=lever-sitekey style="width:300px;height:80px"></div>
  <button id=btn-submit type=button>Submit application</button></form>"""


async def page_with_lever_widget(page):
    await page.evaluate(HOOK)
    await page.set_content(LEVER)
    await page.evaluate("""() => {
      window.hcaptcha = {render: (c, o) => { window.nativeCallback = o.callback; return 1; }, execute: () => {}};
      hcaptcha.render('h-captcha', {sitekey: 'lever-sitekey',
        callback: t => { document.getElementById('hcaptchaResponseInput').value = t; }});
    }""")


async def test_a_silent_native_pass_is_used_and_no_token_is_bought(config, page, monkeypatch):
    config.twocaptcha_api_key, config.captcha_same_ip = "fake-key", True
    await page_with_lever_widget(page)

    async def checkbox_passes_silently(cfg, pg, emit, pending=False):
        await pg.evaluate("window.nativeCallback('browser-own-token')")
        return False, "No hCaptcha image challenge appeared"

    monkeypatch.setattr("jobpilot.captcha_same_ip.solve_same_ip", checkbox_passes_silently)
    provider = AsyncMock(return_value={"token": "solver-ip-token"})
    monkeypatch.setattr("jobpilot.captcha.solve_token", provider)
    events = []
    assert await CaptchaSolver(config, lambda *a: events.append(a)).solve(page)
    assert provider.await_count == 0
    assert await page.locator("#hcaptchaResponseInput").input_value() == "browser-own-token"
    assert any("without a puzzle" in str(e) for e in events)


async def test_no_native_pass_still_falls_back_to_the_provider(config, page, monkeypatch):
    config.twocaptcha_api_key, config.captcha_same_ip = "fake-key", True
    await page_with_lever_widget(page)

    async def nothing_happens(cfg, pg, emit, pending=False):
        return False, "No hCaptcha image challenge appeared"

    monkeypatch.setattr("jobpilot.captcha_same_ip.solve_same_ip", nothing_happens)
    monkeypatch.setattr("jobpilot.captcha.NATIVE_PASS_SECONDS", 0.3)
    provider = AsyncMock(return_value={"token": "solver-ip-token"})
    monkeypatch.setattr("jobpilot.captcha.solve_token", provider)
    await CaptchaSolver(config, lambda *a: None).solve(page)
    assert provider.await_count == 1
