"""A loading sheet over the page refuses every click until the page finishes (Humana's Phenom form after its resume parse,
2026-11-01: two runs ended "Browser control failed at widgets.py:1310" on Next Step). The click waits for the sheet to go.
A real browser, a synthetic page."""

import pytest
from playwright.async_api import async_playwright

from jobpilot.widgets import BUSY_COVER_JS, safe_click, wait_busy_cover

PAGE = """<!doctype html><body style="margin:0"><button id=next style="margin:60px" onclick="document.title='clicked'">Next Step</button>
<div class="overlaybg" style="position:fixed;inset:0;background:#8888"><div class="widget-loader" role="alert" aria-label="loading">
<img alt="loading" width=40 height=40></div></div>
<script>setTimeout(() => document.querySelector('.overlaybg').remove(), %d)</script>"""

DIALOG = """<!doctype html><body style="margin:0"><button id=next style="margin:60px">Next Step</button>
<div role="dialog" aria-label="Loading your profile" style="position:fixed;inset:0;background:#8888"></div>"""


@pytest.mark.browser
async def test_a_click_waits_for_the_loading_sheet_to_go():
    async with async_playwright() as driver:
        browser = await driver.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content(PAGE % 3000)
        await safe_click(page.locator("#next"), timeout=1000)
        assert await page.title() == "clicked"
        await browser.close()


@pytest.mark.browser
async def test_a_dialog_is_not_a_loading_sheet():
    async with async_playwright() as driver:
        browser = await driver.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content(DIALOG)
        handle = await page.locator("#next").element_handle()
        assert not await handle.evaluate(BUSY_COVER_JS)
        assert not await wait_busy_cover(handle, budget=0.5)
        await browser.close()
