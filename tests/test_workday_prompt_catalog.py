"""Workday Field of Study prompts (REA on the apply-flow bundle and 41 failing runs, 2026-10-25).

Workday's default catalog names Computer Science "Computer and Information Science", so the owner's fallback major was
never found; a search returning exactly one item is selected by Workday itself, so probe searches ("Data" -> "Data
Processing") committed values the engine had rejected and left them behind; and an empty search renders no option,
so each one waited out its full timeout. The page's own full value list now decides first, only its exact name is
searched, and pills this call did not choose are removed."""

import time
from pathlib import Path

import pytest

from jobpilot import widgets
from jobpilot.answers.history import major_equivalent

HTML = (Path(__file__).parent / "fixtures" / "workday_prompt.html").read_text()
DEFAULT_TAXONOMY = ["Accounting", "Actuarial Science", "Computer and Information Science", "Computer Engineering",
                    "Computer Graphics", "Data Processing", "Database Management", "Mathematics", "Statistics"]


async def policy(names):
    return major_equivalent("Computer Science", names)


async def open_prompt(page, catalog):
    await page.set_content(HTML)
    await page.evaluate("(c) => { window.CATALOG = c; }", catalog)
    return page.locator("[data-automation-id=multiSelectContainer]")


async def pills(page):
    return await page.evaluate("[...document.querySelectorAll('[data-automation-id=selectedItem]')].map(n => n.innerText.trim())")


@pytest.mark.parametrize(
    "catalog,expected",
    [
        (DEFAULT_TAXONOMY, "Computer and Information Science"),
        (["Accounting", "Computer Science", "Computer Engineering", "Data Processing"], "Computer Science"),
        (["Accounting", "Data Science", "Data Processing"], "Data Science"),
    ],
)
async def test_the_catalog_decides_and_only_its_exact_name_is_searched(page, catalog, expected):
    container = await open_prompt(page, catalog)
    started = time.monotonic()
    committed = await widgets.prompt(page.main_frame, container, ["Data Science", "Computer Science"],
                                     choose=policy, catalog=catalog)
    assert committed == expected
    assert await pills(page) == [expected]
    assert await page.evaluate("window.SEARCHES") == [expected]
    assert time.monotonic() - started < 3


async def test_a_catalog_without_an_acceptable_major_fails_without_touching_the_field(page):
    catalog = ["Accounting", "Actuarial Science", "Mathematics"]
    container = await open_prompt(page, catalog)
    with pytest.raises(ValueError) as caught:
        await widgets.prompt(page.main_frame, container, ["Data Science", "Computer Science"],
                             choose=policy, catalog=catalog)
    assert caught.value.offered["candidates"] == ["Data Science", "Computer Science"]
    assert await pills(page) == [] and await page.evaluate("window.SEARCHES || []") == []


async def test_without_a_catalog_a_rejected_sole_result_is_not_left_selected(page):
    container = await open_prompt(page, DEFAULT_TAXONOMY)

    async def reject(names):
        return None

    with pytest.raises(ValueError):
        await widgets.prompt(page.main_frame, container, "Data Science", choose=reject)
    assert await pills(page) == []  # "Data" -> "Data Processing" was committed by the widget and removed again.


def test_major_equivalence_accepts_only_the_catalogs_name_for_the_same_major():
    assert major_equivalent("Computer Science", DEFAULT_TAXONOMY) == "Computer and Information Science"
    assert major_equivalent("Computer Science", ["Computer and Information Sciences"]) == "Computer and Information Sciences"
    assert major_equivalent("Computer Science", ["Computer Science & Information Tech"]) is None
    assert major_equivalent("Computer Science", ["Computer Engineering", "Data Processing"]) is None
