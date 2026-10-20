"""Owner decision 2026-10-19: iCIMS sends a browser whose invisible hCaptcha shows a puzzle to its login-only Central
page, even after the puzzle is answered; Central cannot create accounts. A clean twin Chrome that Playwright never
attaches to mints the token, and the worker tab hands it to the site's own callback (REA: the email step then opens
/candidate?from=login, the new-account form)."""

import asyncio
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from playwright.async_api import async_playwright

from jobpilot import captcha_twin
from jobpilot.captcha import HOOK, CaptchaSolver

EMAIL_STEP = """<form id=enterEmailForm>
  <input name=css_loginName value="applicant@example.com">
  <div id=h-captcha class=h-captcha data-sitekey=fixture-sitekey data-size=invisible></div>
  <textarea name=h-captcha-response></textarea>
</form>
<script>
  window.hcaptcha = {render: () => 'w1', execute: id => { window.executed = id; }};
  const widget = hcaptcha.render('h-captcha', {sitekey: 'fixture-sitekey', size: 'invisible',
    callback: token => { window.siteToken = token; window.siteCalls = (window.siteCalls || 0) + 1; }});
  hcaptcha.execute(widget);  // the site's Next handler runs execute(); this browser's puzzle stays open
</script>"""


async def open_step(page, url):
    await page.add_init_script(HOOK)
    await page.route("**/*", lambda route: route.fulfill(body=EMAIL_STEP, content_type="text/html"))
    await page.goto(url)


async def test_twin_token_reaches_the_sites_callback_before_any_paid_solver(config, page, monkeypatch):
    config.browser_twin_cdp_url, config.twocaptcha_api_key = "http://127.0.0.1:9", ""
    url = "https://careers-acme.icims.com/jobs/7/engineer/login?in_iframe=1"
    await open_step(page, url)
    twin = AsyncMock(return_value={"token": "twin-token"})
    paid = AsyncMock()
    monkeypatch.setattr("jobpilot.captcha.twin_mint", twin)
    monkeypatch.setattr("jobpilot.captcha.solve_token", paid)
    events = []
    solver = CaptchaSolver(config, lambda *a: events.append(a))
    assert await solver.solve(page)
    assert await page.evaluate("[window.siteToken, window.siteCalls]") == ["twin-token", 1]
    assert await page.locator("textarea").input_value() == "twin-token"
    twin.assert_awaited_once_with("http://127.0.0.1:9", url, "fixture-sitekey")
    paid.assert_not_awaited()
    assert any("twin browser's hCaptcha token delivered" in str(e) for e in events)
    assert "twin-token" not in str(events)
    # One mint per widget generation: a second poll neither re-mints nor re-delivers.
    assert not await solver.solve(page)
    assert twin.await_count == 1


async def test_a_twin_failure_falls_back_to_the_existing_solvers(config, page, monkeypatch):
    config.browser_twin_cdp_url, config.twocaptcha_api_key, config.captcha_same_ip = "http://127.0.0.1:9", "k", False
    await open_step(page, "https://careers-acme.icims.com/jobs/7/engineer/login?in_iframe=1")
    monkeypatch.setattr("jobpilot.captcha.twin_mint", AsyncMock(return_value={"error": "puzzle"}))
    monkeypatch.setattr("jobpilot.captcha.solve_token", AsyncMock(return_value={"token": "paid-token"}))
    events = []
    assert await CaptchaSolver(config, lambda *a: events.append(a)).solve(page)
    assert await page.evaluate("window.siteToken") == "paid-token"
    assert any("did not mint" in str(e) and "puzzle" in str(e) for e in events)


@pytest.mark.parametrize("endpoint,url", [
    ("http://127.0.0.1:9", "https://jobs.lever.co/acme/1/apply"),
    ("", "https://careers-acme.icims.com/jobs/7/engineer/login?in_iframe=1"),
])
async def test_the_twin_serves_only_configured_hosts(config, page, monkeypatch, endpoint, url):
    config.browser_twin_cdp_url, config.twocaptcha_api_key = endpoint, ""
    await open_step(page, url)
    twin = AsyncMock(return_value={"token": "twin-token"})
    monkeypatch.setattr("jobpilot.captcha.twin_mint", twin)
    solver = CaptchaSolver(config, lambda *a: None)
    assert not await solver.solve(page)
    twin.assert_not_awaited()
    assert solver.last_reason == "2Captcha is not configured"


PAGES = {
    "/ok": "<script>window.hcaptcha={render:(el,o)=>{window.o=o;return 1},execute:()=>setTimeout(()=>o.callback('local-token'),30)}</script>",
    "/puzzle": "<script>window.hcaptcha={render:(el,o)=>{window.o=o;return 1},execute:()=>setTimeout(()=>o['open-callback'](),30)}</script>",
    "/away": "<script>location.replace('http://localhost:' + location.port + '/ok')</script>",
}


class Pages(BaseHTTPRequestHandler):
    def do_GET(self):
        body = PAGES.get(self.path.split("?")[0], "").encode()
        self.send_response(200 if body else 404)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
async def twin_chrome(config):
    """A real local Chromium on a debugging port that no Playwright session attaches to."""
    async with async_playwright() as pw:
        path = config.chromium_path or pw.chromium.executable_path
    if not Path(path).exists():
        pytest.skip("Install Chromium: playwright install chromium")
    with tempfile.TemporaryDirectory() as profile:
        process = await asyncio.create_subprocess_exec(
            path, "--headless=new", "--remote-debugging-port=0", f"--user-data-dir={profile}",
            "--no-first-run", "--no-sandbox", "about:blank",
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )
        portfile = Path(profile) / "DevToolsActivePort"
        for _ in range(100):
            if portfile.exists() and portfile.read_text().strip():
                break
            await asyncio.sleep(0.1)
        server = ThreadingHTTPServer(("127.0.0.1", 0), Pages)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            yield f"http://127.0.0.1:{portfile.read_text().splitlines()[0]}", f"http://127.0.0.1:{server.server_port}"
        finally:
            server.shutdown()
            process.terminate()
            await process.wait()


async def test_the_twin_mints_through_plain_cdp(twin_chrome):
    endpoint, site = twin_chrome
    assert await captcha_twin.mint(endpoint, site + "/ok", "site-key", timeout=8) == {"token": "local-token"}
    assert await captcha_twin.mint(endpoint, site + "/puzzle", "site-key", timeout=8) == {"error": "puzzle"}
    left = await captcha_twin.mint(endpoint, site + "/away", "site-key", timeout=8)
    assert left == {"error": "the twin page left the employer host"}


async def test_an_unreachable_or_remote_twin_is_an_error_not_a_crash():
    assert "error" in await captcha_twin.mint("http://127.0.0.1:9", "https://a.icims.com/x", "k", timeout=2)
    assert await captcha_twin.mint("http://203.0.113.5:9225", "https://a.icims.com/x", "k", timeout=2) == {
        "error": "the twin browser must use a local HTTP debugging address"
    }
