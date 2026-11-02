"""A control inside a web component's shadow tree is inside the dialog that holds the component (UKG's modal Close button, 2026-11-01: "Close" failed three
results in a row as covered by the active dialog). The page-level guard hands an unrendered submit button's submit to the control the engine marked
on that form, and to nothing else. A real browser, a synthetic page."""

import pytest
from playwright.async_api import async_playwright

from jobpilot.browser import GUARD
from jobpilot.widgets import ensure_uncovered, safe_click

COMPONENT = """<script>customElements.define('x-close',class extends HTMLElement{connectedCallback(){this.attachShadow({mode:'open'}).innerHTML='<button id=inner onclick="document.title=&quot;closed&quot;">Close</button>'}})</script>"""
DIALOG = """<!doctype html><body>""" + COMPONENT + """<button id=behind>Behind</button><div role="dialog" aria-modal="true" aria-label="Terms" style="position:fixed;inset:80px;background:#fff"><x-close id=host></x-close></div>"""

PROXY = """<!doctype html><body><form id=f onsubmit="event.preventDefault();document.title='submitted'"><input name=a></form>
<button id=visible form=f type=button>Create account</button>
<script>const p=document.createElement('button');p.type='submit';p.value='undefined';p.style.display='none';document.getElementById('f').append(p);
document.getElementById('visible').addEventListener('click',()=>p.click());</script>"""


@pytest.mark.browser
async def test_a_shadow_control_in_the_open_dialog_is_not_covered():
    async with async_playwright() as driver:
        browser = await driver.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content(DIALOG)
        await safe_click(page.locator("#host").locator("#inner"), timeout=2000)
        assert await page.title() == "closed"
        with pytest.raises(ValueError, match="covered"):
            await ensure_uncovered(page.locator("#behind"))
        await browser.close()


async def proxy_page(driver):
    browser = await driver.chromium.launch(headless=True)
    page = await browser.new_page()
    await page.set_content(PROXY)
    await page.evaluate(GUARD)
    return browser, page


@pytest.mark.browser
async def test_the_guard_passes_a_proxy_submit_for_the_control_the_engine_marked():
    async with async_playwright() as driver:
        browser, page = await proxy_page(driver)
        await page.evaluate("""() => {const b=document.getElementById('visible'),f=document.getElementById('f');
          b.dataset.jpAuthControl='true';b.dataset.jpControlLabel='Create account';b.dataset.jpFinalAuth='false';f.__jpAllowedControl=b}""")
        await page.click("#visible")
        assert await page.title() == "submitted"
        await browser.close()


@pytest.mark.browser
async def test_the_guard_still_cancels_a_proxy_submit_nobody_marked():
    async with async_playwright() as driver:
        browser, page = await proxy_page(driver)
        await page.click("#visible")
        assert await page.title() != "submitted"
        await browser.close()


@pytest.mark.browser
async def test_the_guard_still_cancels_a_proxy_for_a_marked_final_submit_that_was_not_authorized():
    async with async_playwright() as driver:
        browser, page = await proxy_page(driver)
        await page.evaluate("""() => {const b=document.getElementById('visible'),f=document.getElementById('f');b.textContent='Submit application';
          b.dataset.jpAuthControl='true';b.dataset.jpControlLabel='Submit application';b.dataset.jpFinalAuth='false';f.__jpAllowedControl=b}""")
        await page.click("#visible")
        assert await page.title() != "submitted"
        await browser.close()
