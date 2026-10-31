"""Search-select pickers read what the control offers and choose by meaning, inside one deadline per field.

Workday's education fields caused 1,025 of the 1,665 field errors after sign-in (2026-10-17..31): the page's own Field of
Study list named the fallback major in its own words ("Computer and Information Science") but the exact-name gate sent
every row to probe searches, whose waits stacked past the form's 12 s limit and left a loading popup open over the next
field. iCIMS's phone country code was compared as text, so "+1 (United States of America)" never matched the row
"United States (1)" that the page already held."""

import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from jobpilot import widgets
from jobpilot.answers import Resolver
from jobpilot.schemas import Profile

HTML = (Path(__file__).parent / "fixtures" / "workday_prompt.html").read_text()
PROFILE = {
    "education": [{"school": "Example University", "degree": "Master of Science", "field_of_study": "Marine Biology"}],
    "answer_rules": {"major_fallback": "Computer Science"},
}


async def open_prompt(page, catalog, delay=250):
    await page.set_content(HTML)
    await page.evaluate("([c, d]) => { window.CATALOG = c; window.DELAY = d; }", [catalog, delay])
    return page.locator("[data-automation-id=multiSelectContainer]")


async def pills(page):
    return await page.evaluate("[...document.querySelectorAll('[data-automation-id=selectedItem]')].map(n => n.innerText.trim())")


async def test_a_prompt_returns_inside_its_deadline_and_closes_its_popup(page):
    # Every search answers after 3 s with rows that are not the value: the stacked waits (the value, its first word,
    # the full list, each category) outran the form's limit and the cancelled write left the popup open.
    container = await open_prompt(page, ["Data Processing", "Database Management", "Accounting"], delay=3000)
    started = time.monotonic()
    with pytest.raises(ValueError):
        await widgets.prompt(page.main_frame, container, "Data Science", deadline=time.monotonic() + 4)
    assert time.monotonic() - started < 6
    assert await page.locator("#popup").is_hidden()
    assert await pills(page) == []


async def test_a_search_that_found_nothing_is_not_typed_again_in_the_run(page):
    container = await open_prompt(page, ["Data Processing", "Database Management", "Accounting"])
    missed = set()
    for _ in range(2):
        with pytest.raises(ValueError):
            await widgets.prompt(page.main_frame, container, "Data Science", missed=missed)
    # "Data Science" is typed on each call; the shorter "Data" only once.
    assert (await page.evaluate("window.SEARCHES")).count("Data") == 1


async def test_a_cold_search_that_shows_no_row_of_the_name_is_typed_once_more(page):
    container = await open_prompt(page, ["Accounting", "Biology", "Computer Science", "Computer Science & Commerce"])
    # The first search answers with the unsearched list (no row of that name), as Workday did for a form's first row.
    await page.evaluate("""() => { const real = window.render; let cold = true;
      window.render = (items, q) => { if (cold && q) { cold = false; return real(window.CATALOG.slice(0, 2), ''); }
        return real(items, q); }; }""")
    committed = await widgets.commit_prompt_name(page.main_frame, container, "Computer Science")
    assert committed == "Computer Science"
    assert await pills(page) == ["Computer Science"]
    assert await page.evaluate("window.SEARCHES") == ["Computer Science", "Computer Science"]
