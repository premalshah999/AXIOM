"""Locator.evaluate costs one protocol call, not three, and keeps every behaviour of the three-call form.

Playwright runs Locator.evaluate as waitForSelector + evaluate + dispose. The form drivers call it about 150 times in
their hot paths; one Workday dropdown write issued 42 protocol calls, 27 of them from this pattern (measured
2026-10-20 with tests/support_roundtrips.py), and a Workday major that the catalog lacks issued 216. On the shared
worker Chrome each call waits for a busy renderer: a plain Yes/No dropdown took 7-9 s at low load in production.

The fast path asks the page for the one matching element in a single evalOnSelectorAll call. When the page does not
have exactly one match it hands over to Playwright's own implementation, so waiting for a late element, the strict
mode error for several matches and the timeout error for a missing one are unchanged.
"""

import pytest
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from jobpilot.playwright_fast import install
from support_roundtrips import roundtrips

install()


async def test_a_present_element_costs_one_round_trip(page):
    await page.set_content("<input id=name value=Alex>")
    with roundtrips() as calls:
        assert await page.locator("#name").evaluate("e => e.value") == "Alex"
    assert calls.total == 1, dict(calls)


@pytest.mark.parametrize(
    "expression,expected",
    [
        ("e => e.id", "box"),
        ("(e) => e.dataset.kind", "tile"),
        ("e=>{return e.tagName}", "DIV"),
        ("function (e) { return e.className }", "wide red"),
        ("async e => { await new Promise(r => setTimeout(r, 20)); return e.id }", "box"),
        ("e => e.getBoundingClientRect().width > 0", True),
        ("e => e.querySelector('i')", None),
        ("e => { e.dataset.touched = '1' }", None),
        ("e => [...e.classList]", ["wide", "red"]),
        ("e => ({id: e.id, kids: e.children.length})", {"id": "box", "kids": 2}),
        ("document.title", "fixture"),
    ],
)
async def test_results_match_playwrights_own_evaluate(page, expression, expected):
    await page.set_content(
        "<title>fixture</title><div id=box class='wide red' data-kind=tile style='width:30px'><b>a</b><b>b</b></div>"
    )
    assert await page.locator("#box").evaluate(expression) == expected


async def test_the_argument_reaches_the_function(page):
    await page.set_content("<div id=box></div>")
    assert await page.locator("#box").evaluate("(e, n) => e.id + n", 5) == "box5"
    assert await page.locator("#box").evaluate("(e, o) => o.a.b", {"a": {"b": "deep"}}) == "deep"


async def test_the_function_runs_exactly_once(page):
    await page.set_content("<div id=box></div>")
    await page.locator("#box").evaluate("e => { window.hits = (window.hits || 0) + 1 }")
    assert await page.evaluate("window.hits") == 1
    with pytest.raises(PlaywrightError, match="boom"):
        await page.locator("#box").evaluate("e => { window.hits++; throw new Error('boom') }")
    assert await page.evaluate("window.hits") == 2


async def test_first_nth_and_scoped_locators_resolve_like_before(page):
    await page.set_content("<ul><li>a</li><li>b</li><li>c</li></ul><p><span>x</span></p>")
    assert await page.locator("li").first.evaluate("e => e.textContent") == "a"
    assert await page.locator("li").nth(2).evaluate("e => e.textContent") == "c"
    assert await page.locator("p").locator("span").evaluate("e => e.textContent") == "x"
    assert await page.get_by_text("b", exact=True).evaluate("e => e.tagName") == "LI"


async def test_shadow_dom_elements_are_found(page):
    await page.set_content("<div id=host></div>")
    await page.evaluate("document.getElementById('host').attachShadow({mode:'open'}).innerHTML = '<input id=inner value=deep>'")
    assert await page.locator("#inner").evaluate("e => e.value") == "deep"


async def test_a_child_frame_locator_evaluates_in_its_own_document(page):
    await page.set_content("<iframe srcdoc=\"<input id=field value=framed>\"></iframe>")
    frame = next(f for f in page.frames if f != page.main_frame)
    await frame.wait_for_selector("#field")
    with roundtrips() as calls:
        assert await frame.locator("#field").evaluate("e => e.value") == "framed"
    assert calls.total == 1, dict(calls)


async def test_several_matches_still_raise_the_strict_mode_error(page):
    await page.set_content("<div class=row></div><div class=row></div>")
    with pytest.raises(PlaywrightError, match="strict mode violation"):
        await page.locator(".row").evaluate("e => e.className")


async def test_a_missing_element_still_times_out_with_playwrights_error(page):
    await page.set_content("<div></div>")
    with pytest.raises(PlaywrightTimeoutError, match=r"Timeout 300ms exceeded"):
        await page.locator("#missing").evaluate("e => 1", timeout=300)


async def test_an_element_that_appears_later_is_waited_for(page):
    await page.set_content(
        "<script>setTimeout(() => document.body.insertAdjacentHTML('beforeend', '<p id=late>here</p>'), 300)</script>"
    )
    assert await page.locator("#late").evaluate("e => e.textContent", timeout=3000) == "here"


async def test_an_element_replaced_during_the_call_is_not_a_stale_handle(page):
    # A handle taken by the old form could be detached before it was used (React remounts inputs on commit).
    await page.set_content("<input id=name value=old>")
    await page.evaluate(
        "document.getElementById('name').addEventListener('blur', e => { const n = e.target.cloneNode(); n.value = 'new'; e.target.replaceWith(n) })"
    )
    await page.locator("#name").focus()
    await page.locator("#name").blur()
    assert await page.locator("#name").evaluate("e => e.value") == "new"
