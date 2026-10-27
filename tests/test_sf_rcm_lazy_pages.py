"""SuccessFactors RCM paginated selects, emulated faithfully (REA on career8.successfactors.com, 2026-10-25): the list box
appears only after a delayed server answer, each page of 100 renders its own <ul> with the same id, later pages load
when the box is scrolled to its end, typing is a debounced server "contains" filter, and only a click commits. Typed
searches ran first and waited for an exact entry, so every answer spelled unlike its option burned the 12 s field
budget (139 deadlines in 36 runs) before the full list and the resolver were tried."""

import asyncio
import json
import time
from pathlib import Path

import pytest

from jobpilot import widgets

HTML = (Path(__file__).parent / "fixtures" / "rcm_paginated_faithful.html").read_text()
FIELD_BUDGET = 12  # forms.FIELD_WRITE_SECONDS

YES_NO = ["No", "Yes"]
VETERAN = ["Non-Veteran", "Not a Protected Veteran", "Prefers not to answer", "Protected Veteran - Disabled"]
COUNTRIES = [f"Country {i:03d}" for i in range(238)] + [
    "United States", "United States Minor Outlying Islands", "Uruguay"] + [f"Land {i:02d}" for i in range(12)]


async def load(page, catalog):
    await page.set_content(HTML.replace("__CATALOG__", json.dumps(catalog)))
    return page.locator("[id='9:_input']")


async def timed(page, catalog, value, choose=None):
    el = await load(page, catalog)
    started = time.monotonic()
    async with asyncio.timeout(FIELD_BUDGET):
        got = await widgets._rcm_paginated(page.main_frame, el, value, choose=choose)
    return el, got, time.monotonic() - started


async def test_a_small_catalog_commits_its_exact_entry(page):
    el, got, took = await timed(page, YES_NO, "Yes")
    assert got == "Yes" and took < 5
    await el.evaluate("e => e.blur()")
    assert await el.input_value() == "Yes"
    assert await page.locator("#tor__fcustq").input_value() == "1"


async def test_an_answer_spelled_differently_reaches_the_resolver_inside_the_budget(page):
    async def choose(texts):
        await asyncio.sleep(1.5)  # A model round trip.
        return "Non-Veteran"

    el, got, took = await timed(page, VETERAN, "I am not a protected veteran", choose=choose)
    assert got in {"Non-Veteran", "Not a Protected Veteran"} and took < 9


async def test_an_entry_on_the_third_page_is_reached(page):
    el, got, took = await timed(page, COUNTRIES, "United States")
    assert got == "United States"
    assert await page.locator("#tor__fcustq").input_value() == str(COUNTRIES.index("United States"))


async def test_an_unoffered_answer_fails_fast_and_closes_the_list(page):
    el = await load(page, VETERAN)
    started = time.monotonic()
    with pytest.raises(ValueError):
        async with asyncio.timeout(FIELD_BUDGET):
            await widgets._rcm_paginated(page.main_frame, el, "Martian")
    assert time.monotonic() - started < 6
    assert await page.locator(".sf-combo-listselect").count() == 0


async def test_a_disabled_dependent_list_is_refused_at_once(page):
    el = await load(page, YES_NO)
    await el.evaluate("e => { e.disabled = true; }")
    started = time.monotonic()
    with pytest.raises(ValueError, match="earlier answer"):
        await widgets._rcm_paginated(page.main_frame, el, "Yes")
    assert time.monotonic() - started < 1


SCHOOLS = [f"University of Somewhere {i:04d}" for i in range(1200)] + ["University of Maryland, College Park"]


async def test_a_school_beyond_the_loaded_pages_is_searched_by_its_distinctive_word(page):
    # Hyundai (2026-10-27): the search used "University", the longest word, and matched every school.
    el, got, took = await timed(page, SCHOOLS, "University of Maryland, College Park")
    assert got == "University of Maryland, College Park"
