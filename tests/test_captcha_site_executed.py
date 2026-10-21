"""Lever's hCaptcha is executed by the site's own Submit (live page read in the REA Chrome, 2026-10-18).

Lever renders a regular hCaptcha into a 0 px #h-captcha whose provider frames are full-screen and visibility:hidden
(inside them hCaptcha mounts frame=checkbox-invisible); its "Submit application" handler calls hcaptcha.execute() and
its onSuccess callback clicks the hidden real submit. The pre-submit pass saw a captured widget, found no puzzle,
bought a token on the solver's IP and handed it to onSuccess, so Lever posted that token and refused it: HTTP 400
"security check" on 47 runs, with no Lever confirmation ever. Such a widget is left to the site before Submit; after
it, a silent pass posts the form by itself and a puzzle is answered in this browser. A token is never bought for it.
"""

from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from jobpilot.captcha import DETECT, HOOK, CaptchaSolver

LEVER = """<form id=application-form>
  <input type=hidden id=hcaptchaResponseInput name=h-captcha-response>
  <div id=h-captcha class=h-captcha data-sitekey=lever-sitekey><iframe
    title="Widget containing checkbox for hCaptcha security challenge"
    style="position:fixed;top:0;left:0;width:100%;height:100%;visibility:hidden"></iframe></div>
  <button id=btn-submit type=button>Submit application</button>
  <button id=hcaptchaSubmitBtn type=submit style="display:none"></button></form>"""

LEVER_SCRIPT = """() => {
  window.hcaptcha = {render: (c, o) => { window.nativeCallback = o.callback; return 'lever-widget'; },
                     execute: () => { window.executions = (window.executions || 0) + 1; }};
  let executedFromSubmitButton = false;
  const input = document.getElementById('hcaptchaResponseInput');
  const onSuccess = token => { input.value = token; if (executedFromSubmitButton) document.getElementById('hcaptchaSubmitBtn').click(); };
  const captchaId = hcaptcha.render('h-captcha', {sitekey: 'lever-sitekey', callback: onSuccess});
  document.getElementById('btn-submit').addEventListener('click', () => {
    if (input.value) { document.getElementById('hcaptchaSubmitBtn').click(); return; }
    executedFromSubmitButton = true;
    hcaptcha.execute(captchaId);
  });
  document.getElementById('application-form').addEventListener('submit', event => {
    event.preventDefault();
    window.posted = (window.posted || []).concat([input.value]);
  });
}"""


@pytest_asyncio.fixture(loop_scope="session")
async def lever(config, page, monkeypatch):
    config.twocaptcha_api_key, config.captcha_same_ip = "fake-key", True
    await page.evaluate(HOOK)
    await page.set_content(LEVER)
    await page.evaluate(LEVER_SCRIPT)
    provider = AsyncMock(return_value={"token": "solver-ip-token"})
    monkeypatch.setattr("jobpilot.captcha.solve_token", provider)
    return provider


async def test_before_submit_the_widget_is_left_to_the_site(config, page, lever, monkeypatch):
    same_ip = AsyncMock(return_value=(False, "No hCaptcha image challenge appeared"))
    monkeypatch.setattr("jobpilot.captcha_same_ip.solve_same_ip", same_ip)
    info = await page.evaluate(DETECT, True)
    assert info["headless"] and info["dormant"]
    solver = CaptchaSolver(config, lambda *a: None)
    assert not await solver.solve(page, pre_submit=True)
    assert not solver.challenge_unverified and not solver.token_refresh_required
    same_ip.assert_not_awaited()
    lever.assert_not_awaited()
    assert await page.locator("#hcaptchaResponseInput").input_value() == ""


async def test_a_silent_pass_after_submit_posts_the_browsers_own_token(config, page, lever, monkeypatch):
    same_ip = AsyncMock(return_value=(False, "No hCaptcha image challenge appeared"))
    monkeypatch.setattr("jobpilot.captcha_same_ip.solve_same_ip", same_ip)
    solver = CaptchaSolver(config, lambda *a: None)
    await solver.solve(page, pre_submit=True)
    await page.click("#btn-submit")
    assert await page.evaluate("window.executions") == 1
    assert not (await page.evaluate(DETECT))["dormant"]
    # hCaptcha is still checking this browser: nothing to answer, nothing to buy, and no unverified challenge.
    assert not await solver.solve(page)
    assert not solver.challenge_unverified
    await page.evaluate("window.nativeCallback('browser-own-token')")
    assert await page.evaluate("window.posted") == ["browser-own-token"]
    assert not await solver.solve(page)
    same_ip.assert_not_awaited()
    lever.assert_not_awaited()


async def test_a_puzzle_after_submit_is_answered_in_this_browser(config, page, lever, monkeypatch):
    async def answered_here(cfg, pg, emit, pending=False):
        assert pending
        await pg.evaluate("window.nativeCallback('browser-own-token')")
        return True, "Website accepted the answer"

    monkeypatch.setattr("jobpilot.captcha_same_ip.solve_same_ip", answered_here)
    solver = CaptchaSolver(config, lambda *a: None)
    await solver.solve(page, pre_submit=True)
    await page.click("#btn-submit")
    monkeypatch.setattr("jobpilot.captcha.challenge_frame", AsyncMock(return_value=(None, None, "hcaptcha nested")))
    assert await solver.solve(page)
    assert await page.evaluate("window.posted") == ["browser-own-token"]
    lever.assert_not_awaited()


async def test_an_unsolved_puzzle_is_never_bought(config, page, lever, monkeypatch):
    monkeypatch.setattr(
        "jobpilot.captcha_same_ip.solve_same_ip", AsyncMock(return_value=(False, "Puzzle rounds exhausted"))
    )
    monkeypatch.setattr("jobpilot.captcha.NATIVE_PASS_SECONDS", 0.2)
    solver = CaptchaSolver(config, lambda *a: None)
    await solver.solve(page, pre_submit=True)
    await page.click("#btn-submit")
    monkeypatch.setattr("jobpilot.captcha.challenge_frame", AsyncMock(return_value=(None, None, "hcaptcha nested")))
    assert not await solver.solve(page)
    assert solver.challenge_unverified and "not solved in this browser" in solver.last_reason
    assert not await solver.solve(page)
    lever.assert_not_awaited()
    assert await page.evaluate("window.posted") is None


async def test_an_uncaptured_lever_widget_is_also_left_to_the_site(config, page, monkeypatch):
    config.twocaptcha_api_key = "fake-key"
    await page.set_content(LEVER)
    provider = AsyncMock(return_value={"token": "solver-ip-token"})
    monkeypatch.setattr("jobpilot.captcha.solve_token", provider)
    info = await page.evaluate(DETECT, True)
    assert info["kind"] == "hcaptcha" and info["dormant"]
    assert not await CaptchaSolver(config, lambda *a: None).solve(page, pre_submit=True)
    provider.assert_not_awaited()


@pytest.mark.parametrize(
    "markup, script",
    [
        # A checkbox the person can see and click.
        (
            "<div id=w class=h-captcha data-sitekey=k><iframe style='width:303px;height:78px'></iframe></div>",
            "hcaptcha.render('w', {sitekey: 'k', callback: () => {}})",
        ),
        # A declared-invisible hCaptcha keeps its own path (bought tokens pass iCIMS and Oracle steps).
        (
            "<div id=w class=h-captcha data-sitekey=k><iframe style='visibility:hidden'></iframe></div>",
            "hcaptcha.render('w', {sitekey: 'k', size: 'invisible', callback: () => {}}); hcaptcha.execute()",
        ),
        # An invisible reCAPTCHA whose badge the site hides with CSS.
        (
            "<div id=w data-sitekey=k><iframe style='visibility:hidden'></iframe></div>",
            "grecaptcha.render('w', {sitekey: 'k', size: 'invisible', callback: () => {}}); grecaptcha.execute(0)",
        ),
    ],
)
async def test_other_widgets_are_not_treated_as_site_executed(page, markup, script):
    await page.evaluate(HOOK)
    await page.set_content(f"<form>{markup}</form>")
    await page.evaluate(
        "() => { window.hcaptcha = {render: () => 'h', execute: () => {}};"
        " window.grecaptcha = {render: () => 0, execute: () => {}}; }"
    )
    await page.evaluate(script)
    info = await page.evaluate(DETECT, True)
    assert info and not info["headless"] and not info.get("dormant")


async def test_a_site_that_posts_during_the_puzzle_is_left_to_the_page(config, page, monkeypatch):
    # Lever 2026-10-18: hCaptcha accepted the answers, onSuccess posted the form and Lever's 400 page replaced the
    # document while the solver still waited for a token on the old one.
    config.twocaptcha_api_key, config.captcha_same_ip = "fake-key", True
    await page.route("https://jobs.lever.co/**", lambda route: route.fulfill(body=LEVER, content_type="text/html"))
    await page.add_init_script(HOOK)
    await page.goto("https://jobs.lever.co/acme/1/apply")
    await page.evaluate(LEVER_SCRIPT)
    provider = AsyncMock(return_value={"token": "solver-ip-token"})
    monkeypatch.setattr("jobpilot.captcha.solve_token", provider)

    async def site_posts_meanwhile(cfg, pg, emit, pending=False):
        await pg.goto("https://jobs.lever.co/acme/1/apply")
        return False, "Image challenge closed without a response token"

    monkeypatch.setattr("jobpilot.captcha_same_ip.solve_same_ip", site_posts_meanwhile)
    solver = CaptchaSolver(config, lambda *a: None)
    await solver.solve(page, pre_submit=True)
    await page.click("#btn-submit")
    monkeypatch.setattr("jobpilot.captcha.challenge_frame", AsyncMock(return_value=(None, None, "hcaptcha nested")))
    assert not await solver.solve(page)
    assert not solver.challenge_unverified and "moved on" in solver.last_reason
    provider.assert_not_awaited()
