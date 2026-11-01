"""A focused empty field shows its required message when the mouse goes down elsewhere; the message moves the control away
before the mouse comes up, and the click is lost. A real browser, a synthetic page."""

from types import SimpleNamespace

import pytest
from playwright.async_api import async_playwright

from jobpilot.adapters.base import Adapter

PAGE = """<!doctype html><body style="margin:40px"><label>Email <input id=e></label><div id=m></div><br>
<a id=l href="#" onclick="document.title='clicked';return false">Create an account</a>
<script>e.addEventListener('blur',()=>{ if(!e.value) m.innerHTML='<div style="height:48px">An email address is required.</div>' })</script>"""


@pytest.mark.browser
async def test_a_control_below_a_focused_empty_field_receives_its_click():
    async with async_playwright() as driver:
        browser = await driver.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content(PAGE)
        await page.focus("#e")
        adapter = Adapter.__new__(Adapter)
        adapter.e = SimpleNamespace(form=SimpleNamespace(page=page))
        await adapter.leave_focused_field()
        await page.click("#l")
        await page.wait_for_timeout(200)
        assert await page.title() == "clicked"
        await browser.close()


async def test_a_page_that_cannot_answer_is_no_reason_to_stop():
    adapter = Adapter.__new__(Adapter)
    adapter.e = SimpleNamespace(form=SimpleNamespace(page=None))
    await adapter.leave_focused_field()
