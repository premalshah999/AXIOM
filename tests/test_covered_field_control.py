"""Humana (Workday, 2026-10-30): an optional Field of Study search left its results popup open and still loading
(no listbox yet) over the required Degree button above it. dismiss_pickers saw no open list, every click on Degree was
refused as "intercepts pointer events", its options were never read and the run stopped on that field. A field
control covered by a popup is now cleared with real pointer input, once, and clicked."""

import time

import pytest
from playwright.async_api import Error as PlaywrightError

from jobpilot import widgets

PAGE = """<style>body{margin:0} #degree{position:absolute;left:150px;top:150px;width:200px;height:30px}
#popup{position:absolute;left:100px;top:100px;width:300px;height:200px;background:#eee}</style>
<button id=degree aria-haspopup=listbox type=button>Select One</button>
<div id=popup class=loading>...</div>
<script>
window.clicks = 0; window.pointerdowns = 0;
degree.addEventListener('click', () => { window.clicks++ });
document.addEventListener('mousedown', e => { window.pointerdowns++; %s });
document.addEventListener('keydown', e => { %s });
</script>"""


async def open_page(page, on_mousedown="", on_keydown=""):
    await page.set_content(PAGE % (on_mousedown, on_keydown))


async def test_a_loading_popup_over_a_field_is_closed_by_a_real_click_and_the_field_is_clicked(page):
    # Closes only on a trusted outside click: the synthetic one dismiss_pickers sends first is ignored.
    await open_page(page, "if (e.isTrusted && !popup.contains(e.target)) popup.remove()")
    started = time.monotonic()
    await widgets.field_click(page.locator("#degree"), timeout=4000)
    assert await page.evaluate("window.clicks") == 1
    assert await page.locator("#popup").count() == 0
    assert time.monotonic() - started < 3, "the native click's 4 s wait is not paid first"


async def test_a_popup_that_closes_on_escape_only_is_closed_too(page):
    await open_page(page, "", "if (e.key === 'Escape') popup.remove()")
    await widgets.field_click(page.locator("#degree"), timeout=4000)
    assert await page.evaluate("window.clicks") == 1


async def test_a_popup_that_will_not_close_still_refuses_the_click(page):
    await open_page(page)
    with pytest.raises(PlaywrightError):
        await widgets.field_click(page.locator("#degree"), timeout=800)
    assert await page.evaluate("window.clicks") == 0


async def test_a_modal_dialog_over_a_field_is_never_dismissed_by_a_blank_click(page):
    await page.set_content(
        """<button id=degree type=button style="position:absolute;left:150px;top:150px">Select One</button>
        <div role=dialog aria-modal=true style="position:fixed;inset:0;background:#fff8">Terms</div>
        <script>window.pointerdowns = 0; document.addEventListener('mousedown', () => window.pointerdowns++)</script>"""
    )
    with pytest.raises(ValueError):
        await widgets.field_click(page.locator("#degree"), timeout=800)
    assert await page.evaluate("window.pointerdowns") == 0
