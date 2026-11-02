"""A control inside a web component's shadow tree is inside the dialog that holds the component (UKG's modal Close button, 2026-11-01: "Close" failed three
results in a row as covered by the active dialog). The page guard is unchanged: it cancels a hidden submit button's submit, and only the one button the engine
marks for one account click passes. A real browser, a synthetic page."""

import pytest
from playwright.async_api import async_playwright

from jobpilot.browser import GUARD
from jobpilot.forms import MARK_BOUND_PROXY, UNMARK_BOUND_PROXY
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


async def proxy_page(driver, html=PROXY):
    browser = await driver.chromium.launch(headless=True)
    page = await browser.new_page()
    await page.set_content(html)
    await page.evaluate(GUARD)
    return browser, page


def bound(page):
    return page.locator("#visible").evaluate(MARK_BOUND_PROXY)


@pytest.mark.browser
async def test_the_guard_cancels_a_hidden_button_nobody_marked():
    async with async_playwright() as driver:
        browser, page = await proxy_page(driver)
        await page.click("#visible")
        assert await page.title() != "submitted"
        await browser.close()


@pytest.mark.browser
async def test_a_mark_on_the_visible_control_does_not_pass_the_hidden_button():
    async with async_playwright() as driver:
        browser, page = await proxy_page(driver)
        await page.evaluate("""() => {const b=document.getElementById('visible'),f=document.getElementById('f');
          b.dataset.jpAuthControl='true';b.dataset.jpControlLabel='Create account';b.dataset.jpFinalAuth='false';f.__jpAllowedControl=b}""")
        await page.click("#visible")
        assert await page.title() != "submitted"
        await browser.close()


@pytest.mark.browser
async def test_the_one_bound_button_passes_for_this_click_and_not_after_it():
    async with async_playwright() as driver:
        browser, page = await proxy_page(driver)
        assert await bound(page)
        await page.click("#visible")
        assert await page.title() == "submitted"
        await page.locator("#visible").evaluate(UNMARK_BOUND_PROXY)
        await page.evaluate("document.title=''")
        await page.click("#visible")
        assert await page.title() != "submitted"
        await browser.close()


@pytest.mark.browser
@pytest.mark.parametrize(
    "html",
    [
        PROXY.replace("<input name=a>", "<input name=a><input type=file name=resume>"),
        PROXY.replace("<input name=a>", "<input name=a><button type=submit style='display:none'></button>"),
        PROXY.replace("p.value='undefined'", "p.value='Submit application'"),
        PROXY.replace("form=f type=button", "type=button"),
    ],
    ids=["a_form_with_a_resume", "two_hidden_buttons", "final_words_on_the_button", "a_control_that_names_no_form"],
)
async def test_no_other_form_or_button_is_marked(html):
    async with async_playwright() as driver:
        browser, page = await proxy_page(driver, html)
        assert not await bound(page)
        await page.click("#visible")
        assert await page.title() != "submitted"
        await browser.close()
