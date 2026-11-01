"""Paylocity (2026-11-01, run 831b98ee): Country and State are select widgets whose chosen-value element lies over the
text input inside a wrapper marked aria-haspopup, with no `control` class anywhere. Every click on the input was refused
as "intercepts pointer events" and the form never got past Country. A cover that belongs to the input's own widget
opens the widget; a cover outside it (a chat bubble) is still a cover."""

import pytest
from playwright.async_api import Error as PlaywrightError

from jobpilot import widgets

PAGE = """<style>body{margin:0}
.box{position:relative;width:300px;height:36px;border:1px solid #888;margin:20px}
.value{position:absolute;left:0;top:0;right:30px;bottom:0;padding:8px;background:#fff}
.sel-input{position:absolute;left:4px;top:8px;width:6px;border:0}
.clear{position:absolute;right:0;top:0;width:28px;height:36px} .menu{margin:0 20px}</style>
<div class=box id=wrap aria-haspopup=listbox aria-expanded=false>
  <input id=country type=text class=sel-input>
  <div class=value id=val>United States</div>
  <div class=clear id=clear tabindex=0><i aria-label="delete" role=img>x</i></div>
</div>
<div class=menu id=menu></div>
%s
<script>
window.opened = 0; window.cleared = 0;
const OPTIONS = ['Canada', 'United States'];
function open() { window.opened++; wrap.setAttribute('aria-expanded', 'true');
  menu.innerHTML = OPTIONS.map(t => '<div role=option class=opt>' + t + '</div>').join(''); }
// the widget opens on a press of its own face, never on the bare input
wrap.addEventListener('mousedown', e => { if (!e.target.closest('#clear')) open(); else window.cleared++; });
</script>"""


async def test_a_value_element_over_the_input_opens_the_widget(page):
    await page.set_content(PAGE % "")
    await widgets.open_combobox(page.locator("#country"))
    assert await page.evaluate("window.opened") == 1
    assert await page.evaluate("window.cleared") == 0
    assert await page.locator(".opt").count() == 2


async def test_a_chat_bubble_over_the_input_is_still_a_cover_and_is_not_clicked_through(page):
    await page.set_content(
        PAGE % '<div id=chat style="position:fixed;left:0;top:0;width:400px;height:120px;background:#ccc">Chat</div>'
    )
    with pytest.raises((PlaywrightError, ValueError, TimeoutError)):
        await widgets.open_combobox(page.locator("#country"))
    assert await page.evaluate("window.opened") == 0


async def test_the_widgets_clear_control_is_not_used_to_open_it(page):
    # The input sits under the clear control only: clicking that would erase the value.
    await page.set_content(PAGE.replace("left:4px;top:8px;width:6px", "left:275px;top:8px;width:6px") % "")
    with pytest.raises((PlaywrightError, ValueError, TimeoutError)):
        await widgets.open_combobox(page.locator("#country"))
    assert await page.evaluate("window.cleared") == 0
