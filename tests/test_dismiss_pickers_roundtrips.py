"""dismiss_pickers is one protocol call when no list stays open.

Every picker write (dropdown, combobox, prompt) starts by closing whatever list is open. That used to be an outside
click, a toggle of open Workday-style pickers and a wait_for_function that answered at once on a quiet page: five
protocol calls before the first option was read, on a renderer that is shared with the other workers.
"""

import time

from jobpilot import widgets
from support_roundtrips import roundtrips


async def test_nothing_open_costs_one_call(page):
    await page.set_content("<label>Name<input></label><button>Next</button>")
    with roundtrips() as calls:
        await widgets.dismiss_pickers(page.main_frame)
    assert calls.total == 1, dict(calls)


async def test_a_workday_style_picker_that_ignores_outside_clicks_is_toggled_closed(page):
    await page.set_content(
        """<button id=trigger aria-haspopup=listbox aria-expanded=true type=submit>Select One</button>
        <ul id=list role=listbox><li role=option>Yes</li></ul>
        <script>
        trigger.onclick = e => { e.preventDefault(); const open = list.hidden; list.hidden = !open;
          trigger.setAttribute('aria-expanded', String(open)) };
        document.addEventListener('click', () => {}, true);
        </script>"""
    )
    await widgets.dismiss_pickers(page.main_frame)
    assert await page.locator("#list").is_hidden()
    assert await page.locator("#trigger").get_attribute("aria-expanded") == "false"
    assert await page.locator("#trigger").get_attribute("type") == "submit", "the trigger's own type must be restored"


async def test_a_list_closed_by_the_outside_click_needs_no_further_call(page):
    await page.set_content(
        """<input id=box><ul id=list role=listbox><li role=option>Yes</li></ul>
        <script>document.body.addEventListener('click', () => { list.hidden = true })</script>"""
    )
    with roundtrips() as calls:
        await widgets.dismiss_pickers(page.main_frame)
    assert await page.locator("#list").is_hidden()
    assert calls.total == 1, dict(calls)


async def test_a_list_that_closes_a_moment_later_is_waited_for(page):
    await page.set_content(
        """<ul id=list role=listbox><li role=option>Yes</li></ul>
        <script>document.body.addEventListener('click', () => setTimeout(() => { list.hidden = true }, 150))</script>"""
    )
    await widgets.dismiss_pickers(page.main_frame)
    assert await page.locator("#list").is_hidden()


async def test_a_list_that_will_not_close_still_gets_the_escape_fallback(page):
    await page.set_content(
        """<ul id=list role=listbox><li role=option>Yes</li></ul>
        <script>document.addEventListener('keydown', e => { if (e.key === 'Escape') list.hidden = true })</script>"""
    )
    started = time.monotonic()
    await widgets.dismiss_pickers(page.main_frame)
    assert await page.locator("#list").is_hidden()
    assert time.monotonic() - started < 5


async def test_a_menu_without_items_is_not_an_open_picker(page):
    """Workable's uploaded-resume card keeps its view/delete links in a permanent role=menu; every dropdown after the
    upload was refused as "Previous dropdown remains open"."""
    await page.set_content(
        """<section><div role=menu><div role=group><a href="#" aria-label="view resume.pdf">view</a>
        <button type=button aria-label="delete resume.pdf">delete</button></div></div></section>
        <input id=box role=combobox aria-controls=list aria-expanded=false>
        <ul id=list role=listbox hidden><li role=option>Available immediately</li></ul>"""
    )
    with roundtrips() as calls:
        await widgets.dismiss_pickers(page.main_frame)
    assert calls.total == 1, dict(calls)
    await page.evaluate("list.hidden = false")
    assert await page.main_frame.evaluate(widgets.COUNT_OPEN_LISTS_JS) == 1, "a real list still counts as open"


async def test_a_sites_navigation_menu_is_not_an_open_picker(page):
    """IXL's careers page (2026-10-29) has a visible header role=menu of links inside a <nav>; the school and field of
    study pickers below it were refused as "Previous dropdown remains open" and never opened."""
    await page.set_content(
        """<header><nav><ul role=menu class=company-nav-list>
        <li role=none><a role=menuitem href="/company/mission">Our mission</a></li>
        <li role=none><a role=menuitem href="/company/careers">Careers</a></li></ul></nav></header>
        <ul role=menu id=links><li role=none><a role=menuitem href="https://example.test/a">Docs</a></li></ul>
        <input id=box role=combobox aria-controls=list aria-expanded=false>
        <ul id=list role=listbox hidden><li role=option>Computer Science</li></ul>"""
    )
    with roundtrips() as calls:
        await widgets.dismiss_pickers(page.main_frame)
    assert calls.total == 1, dict(calls)
    await page.evaluate("list.hidden = false")
    assert await page.main_frame.evaluate(widgets.COUNT_OPEN_LISTS_JS) == 1, "a real list still counts as open"
    # An action menu of buttons (not links) outside a nav is still a menu the next picker must wait out.
    await page.set_content(
        """<ul role=menu id=m><li role=menuitem tabindex=0>Edit</li><li role=menuitem tabindex=0>Delete</li></ul>"""
    )
    assert await page.main_frame.evaluate(widgets.COUNT_OPEN_LISTS_JS) == 1
