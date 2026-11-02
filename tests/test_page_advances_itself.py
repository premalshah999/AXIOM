"""A step the page advances by itself is not a commit: the guard lets a script's click on a field-less form through."""

import pytest

from jobpilot.browser import GUARD

# Synthetic replica of a wizard interstitial: one visually hidden form, display:none fields, a lone "Submit" that the page's
# own script presses on load. The form posts to /next on the local fixture origin.
STEP = """<style>.hv{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0)} .gone{display:none}</style>
<form class="hv" method="post" action="/next"><input type="hidden" name="t" value="1">
<div class="gone"><input type="date" name="d" value="2026-01-01"><select name="s"><option>x</option></select></div>
<button type="submit" id="go">Submit</button></form>
<script>SCRIPT</script>"""


def step(script):
    return STEP.replace("SCRIPT", script)


FIELDS = '<input type="text" name="name" value="" id="n">'


async def run_page(config, tmp_path, html, click_by):
    from playwright.async_api import async_playwright

    seen = []
    async with async_playwright() as pw:
        context = await pw.chromium.launch_persistent_context(
            str(tmp_path / "synthetic-chrome"), executable_path=config.chromium_path or None, headless=True,
            args=["--no-sandbox", "--use-mock-keychain"])
        try:
            await context.add_init_script(GUARD)

            async def handle(route):
                if route.request.method == "POST":
                    seen.append(route.request.url)
                    return await route.fulfill(status=200, content_type="text/html", body="<p>next</p>")
                await route.fulfill(status=200, content_type="text/html", body=html)

            await context.route("http://fixture.test/**", handle)
            page = await context.new_page()
            await page.goto("http://fixture.test/step")
            if click_by == "trusted":
                await page.evaluate("document.querySelector('#go').style.cssText='position:fixed;top:0;left:0;width:80px;height:30px'")
                await page.locator("#go").click(force=True)
            await page.wait_for_timeout(800)
        finally:
            await context.close()
    return seen


@pytest.mark.browser
async def test_script_click_on_fieldless_step_goes_through(config, tmp_path):
    html = step("window.addEventListener('load',()=>document.querySelector('#go').click())")
    assert await run_page(config, tmp_path, html, "script") == ["http://fixture.test/next"]


@pytest.mark.browser
async def test_script_click_on_form_with_a_field_is_still_blocked(config, tmp_path):
    html = step("window.addEventListener('load',()=>document.querySelector('#go').click())")
    html = html.replace('<button', FIELDS + '<button')
    assert await run_page(config, tmp_path, html, "script") == []


@pytest.mark.browser
async def test_trusted_submit_click_on_fieldless_step_is_still_blocked(config, tmp_path):
    html = step("")
    assert await run_page(config, tmp_path, html, "trusted") == []
