"""Callback-driven site changes must settle before classifying a CAPTCHA blocker."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from jobpilot.browser import BrowserEngine
from jobpilot.captcha import HOOK


@pytest.mark.parametrize("advance", [True, False])
async def test_callback_navigation_is_observed_without_a_second_purchase(page, service, monkeypatch, advance):
    service.config.twocaptcha_api_key = "synthetic-key"
    await page.set_content("""<div id=widget class=h-captcha data-sitekey=fixture></div>
      <iframe title="hCaptcha challenge" style="width:300px;height:200px" srcdoc="Challenge"></iframe>""")
    await page.evaluate(HOOK)
    await page.evaluate(
        """advance => {
      window.hcaptcha={render:()=>1};
      hcaptcha.render('widget',{sitekey:'fixture',callback:()=>{
        if(advance)setTimeout(()=>{
          document.querySelector('iframe').remove();
          document.querySelector('#widget').remove();
          document.body.insertAdjacentHTML('beforeend','<h1>Password step</h1>');
        },200);
      }});
    }""",
        advance,
    )
    purchase = AsyncMock(return_value={"token": "synthetic-token"})
    monkeypatch.setattr("jobpilot.captcha.solve_token", purchase)
    engine = BrowserEngine(service, "synthetic-run")
    engine.page = page
    engine.form = SimpleNamespace(page=page)
    result = await engine.browser_challenge()
    assert purchase.await_count == 1
    if advance:
        assert result is None
        assert await page.get_by_role("heading", name="Password step").count() == 1
    else:
        assert result["state"] == "waiting_browser"
        assert result["blocker_type"] == "captcha"
        assert await page.locator("iframe").count() == 1
