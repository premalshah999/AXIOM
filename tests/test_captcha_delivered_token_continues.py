"""A delivered token whose provider frame stays drawn is not a refusal (Ring Power iCIMS 2026-10-28, Vigil Labs 2026-10-29:
"Token delivered to website callback. The employer page remains open in Chrome."). The page's own control is pressed by
the caller; a transient solver outage is retried, and a purchase that outlives a single-page app's route change is kept."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from jobpilot import captcha_client, captcha_visual
from jobpilot.browser import BrowserEngine
from jobpilot.captcha import HOOK, CaptchaSolver

FORM = """<form><input name=email value="alex@example.test">
  <div id=widget class=h-captcha data-sitekey=fixture style="height:80px"></div>
  <textarea name=h-captcha-response></textarea><button type=button>Submit</button></form>
  <iframe title="hCaptcha challenge" style="width:300px;height:200px" srcdoc="Challenge"></iframe>"""


async def engine_on(page, service, monkeypatch, clear=False):
    service.config.twocaptcha_api_key = "synthetic-key"
    await page.set_content(FORM)
    await page.evaluate(HOOK)
    await page.evaluate(
        """clear => { window.hcaptcha={render:()=>1};
      hcaptcha.render('widget',{sitekey:'fixture',callback:()=>{
        if(clear)document.querySelector('textarea').value='';}}); }""",
        clear,
    )
    monkeypatch.setattr("jobpilot.captcha.solve_token", AsyncMock(return_value={"token": "synthetic-token"}))
    engine = BrowserEngine(service, "synthetic-run")
    engine.page, engine.form = page, SimpleNamespace(page=page)
    return engine


async def test_held_token_with_frame_still_drawn_lets_the_page_act_then_refuses(page, service, monkeypatch):
    engine = await engine_on(page, service, monkeypatch)
    adapter = SimpleNamespace(inbox=SimpleNamespace(live=lambda challenge: False), challenge=None)
    assert await engine.browser_challenge(adapter=adapter) is None
    assert await page.locator("textarea").input_value() == "synthetic-token"
    # The page did not move: later passes are bounded, then the frame is a refusal.
    results = [await engine.browser_challenge(adapter=adapter) for _ in range(3)]
    assert results[:2] == [None, None]
    assert results[2]["blocker_type"] == "captcha"


async def test_a_token_the_page_emptied_again_is_not_held(page, service, monkeypatch):
    engine = await engine_on(page, service, monkeypatch, clear=True)
    adapter = SimpleNamespace(inbox=SimpleNamespace(live=lambda challenge: False), challenge=None)
    result = await engine.browser_challenge(adapter=adapter)
    assert result is None or result["blocker_type"] == "captcha"
    assert not await engine.captcha.token_held(page)


async def test_without_an_adapter_the_frame_still_parks(page, service, monkeypatch):
    engine = await engine_on(page, service, monkeypatch)
    result = await engine.browser_challenge()
    assert result["state"] == "waiting_browser" and result["blocker_type"] == "captcha"


async def test_route_change_in_the_same_document_keeps_a_purchased_token(config, page, monkeypatch):
    config.twocaptcha_api_key = "fake-key"
    await page.add_init_script(HOOK)
    await page.route(
        "**/*",
        lambda route: route.fulfill(
            body="<div id=widget class=g-recaptcha data-sitekey=owned-key></div>", content_type="text/html"
        ),
    )
    await page.goto("https://spa.example.test/auth/register")
    await page.evaluate("""() => { grecaptcha={render:()=>1};
      grecaptcha.render('widget',{sitekey:'owned-key',callback:t=>{window.got=t;}}); }""")

    async def solve(*args, **kwargs):
        await page.evaluate("history.pushState({}, '', '/auth/login')")
        return {"token": "purchased-token"}

    monkeypatch.setattr("jobpilot.captcha.solve_token", solve)
    assert await CaptchaSolver(config, lambda *a: None).solve(page)
    assert await page.evaluate("window.got") == "purchased-token"


async def test_a_refused_delivery_says_what_changed(config, page, monkeypatch):
    config.twocaptcha_api_key = "fake-key"
    await page.set_content("<div id=widget class=g-recaptcha data-sitekey=owned-key></div>")
    await page.evaluate(HOOK)
    await page.evaluate("grecaptcha={render:()=>1};grecaptcha.render('widget',{sitekey:'owned-key',callback:()=>{}})")

    async def solve(*args, **kwargs):
        await page.evaluate("__jpTokenCaptcha.entries[0].generation++")
        return {"token": "purchased-token"}

    monkeypatch.setattr("jobpilot.captcha.solve_token", solve)
    events = []
    assert not await CaptchaSolver(config, lambda *a: events.append(a)).solve(page)
    assert events[-1][2]["why"] == ["generation"]


async def test_connection_errors_to_the_solver_are_retried_with_backoff(monkeypatch):
    once = AsyncMock(side_effect=[{"error": "SOLVER_CONNECTION_ERROR"}] * 2 + [{"token": "t"}])
    pauses = []

    async def sleep(seconds):
        pauses.append(seconds)

    monkeypatch.setattr(captcha_client, "solve_token_once", once)
    monkeypatch.setattr(captcha_client.asyncio, "sleep", sleep)
    assert await captcha_client.solve_token("k", {"kind": "hcaptcha"}, "https://example.test", 30) == {"token": "t"}
    assert pauses == [3, 9]


async def test_rejections_and_timeouts_are_not_retried(monkeypatch):
    once = AsyncMock(return_value={"error": "SOLVER_REJECTED"})
    monkeypatch.setattr(captcha_client, "solve_token_once", once)
    assert (await captcha_client.solve_token("k", {}, "https://example.test", 30))["error"] == "SOLVER_REJECTED"
    once.assert_awaited_once()


async def test_image_fallback_repeats_a_dropped_request(monkeypatch):
    calls = []

    class Client:
        async def post(self, path, json):
            calls.append(path)
            if len(calls) < 3:
                raise httpx.ConnectError("down")
            return httpx.Response(200, json={"errorId": 0, "status": "ready", "solution": {"x": 1}},
                                  request=httpx.Request("POST", "https://example.test" + path))

    async def sleep(seconds):
        pass

    monkeypatch.setattr(captcha_visual.asyncio, "sleep", sleep)
    response = await captcha_visual.post(Client(), "/createTask", {})
    assert response.json()["status"] == "ready" and len(calls) == 3


async def test_image_fallback_gives_up_after_the_last_pause(monkeypatch):
    async def sleep(seconds):
        pass

    class Client:
        async def post(self, path, json):
            raise httpx.ConnectError("down")

    monkeypatch.setattr(captcha_visual.asyncio, "sleep", sleep)
    with pytest.raises(httpx.ConnectError):
        await captcha_visual.post(Client(), "/createTask", {})
