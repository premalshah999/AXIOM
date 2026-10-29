"""Greenhouse react-select comboboxes commit fast, or fail fast, instead of waiting out their deadline.

Production (confirmed or not, 2026-10-18 to 10-20): Greenhouse comboboxes hit the 12 s write budget (30 s for School)
on Location (City) 25 times, Degree 22, School 17-20 and Country 14, and the rest averaged 5-10 s a write. Two
waits in widgets.combobox burned the time on a typed search:

- the list never contained the query text ("City, MD" against "City, Maryland, United States"), so the wait for a
  matching list ran its whole 4 s although the list had settled and the applicant's own address picks from it;
- a search that found nothing leaves the menu saying "No options", which is no option row, so an unlisted school
  waited 4 s, then again 2 s for each alias and for "Other".

The synthetic pages below copy the observed ids, classes and menu states (docs/rea/maps/greenhouse.md); all names
are invented. Times are for a quiet machine; each bound sits well under the wait it replaces.
"""

import time

import pytest

from jobpilot import widgets
from support_react_select import markup

CITIES = [
    "Rivertown, Maryland, United States",
    "Rivertown, Ohio, United States",
    "Rivertown Heights, Maryland, United States",
]
SCHOOLS = ["Aldermoor University", "Brightwater College", "Cedar Ridge Institute of Technology"]


def maryland_only(texts):
    """The applicant's own address decides, as FormSession.location_resolver does."""
    matches = [t for t in texts if t.split(",")[0] == "Rivertown" and "Maryland" in t]
    return matches[0] if len(matches) == 1 else None


async def pick(page, html, field_id, value, **kwargs):
    await page.set_content(html)
    started = time.monotonic()
    try:
        return await widgets.combobox(page.main_frame, page.locator("#" + field_id), value, **kwargs), None, time.monotonic() - started
    except ValueError as exc:
        return None, str(exc), time.monotonic() - started


async def test_a_city_with_a_state_abbreviation_commits_without_waiting_for_a_query_match(page):
    html = markup("candidate-location", "Location (City)", CITIES, remote=True)
    got, error, seconds = await pick(page, html, "candidate-location", "Rivertown, MD", resolve=maryland_only)
    assert got == "Rivertown, Maryland, United States", error
    assert await page.evaluate("window.picked") == "Rivertown, Maryland, United States"
    assert seconds < 3.8, f"waited the whole typed-search timeout: {seconds:.1f}s"


async def test_the_wrong_state_is_never_picked_to_save_time(page):
    html = markup("candidate-location", "Location (City)", CITIES[1:], remote=True)  # no Maryland result at all
    got, error, seconds = await pick(page, html, "candidate-location", "Rivertown, OH", resolve=maryland_only)
    assert got is None and "No matching option" in error
    assert await page.evaluate("window.picked") is None


async def test_an_unlisted_school_fails_fast_when_the_menu_says_no_options(page):
    html = markup("school--0", "School", SCHOOLS, remote=True, open_catalog=True)
    got, error, seconds = await pick(page, html, "school--0", "Nowhere State University", school_scope=True)
    assert got is None and "No matching option" in error
    assert await page.evaluate("window.picked") is None
    assert seconds < 5.5, f"waited out the empty search: {seconds:.1f}s"  # 7.0 s before the fix


async def test_a_slow_search_is_not_taken_for_no_results(page):
    # The menu says "Loading..." for 1.6 s and a "No options" flash precedes it during the debounce.
    html = markup("school--0", "School", SCHOOLS, remote=True, open_catalog=True, debounce=500, latency=1600, flash_empty=True)
    got, error, _ = await pick(page, html, "school--0", "Brightwater College", school_scope=True)
    assert got == "Brightwater College", error


async def test_menu_notice_reads_the_menu_state(page):
    await page.set_content('<div class="select__menu"><div class="select__menu-notice--loading">Loading...</div></div>')
    assert await widgets.menu_notice(page.main_frame) == "loading"
    await page.set_content('<div class="select__menu"><div class="select__menu-notice--no-options">No options</div></div>')
    assert await widgets.menu_notice(page.main_frame) == "none"
    await page.set_content('<div class="select__menu"><div class="select__menu-notice--no-options">Start typing to search</div></div>')
    assert await widgets.menu_notice(page.main_frame) == ""
    await page.set_content('<div class="select__menu" hidden><div class="select__menu-notice--no-options">No options</div></div>')
    assert await widgets.menu_notice(page.main_frame) == ""


@pytest.mark.parametrize("value,expected", [("Bachelor's Degree", "Bachelor's Degree"), ("Bachelor of Science", "Bachelor's Degree")])
async def test_a_static_list_still_commits_and_reads_back(page, value, expected):
    degrees = ["Associate's Degree", "Bachelor's Degree", "Master's Degree", "Doctorate", "Other"]
    got, error, _ = await pick(page, markup("degree--0", "Degree", degrees), "degree--0", value)
    assert got == expected, error
    assert await page.evaluate("window.picked") == expected


MAJORS = ["Art", "Computer Science", "Mathematics", "Statistics & Decision Theory"]


def major_pick(value, fallback):
    def pick_major(texts):
        for wanted in (value, fallback):
            named = [t for t in texts if t == wanted]
            if len(named) == 1:
                return named[0]
        return None

    return pick_major


async def test_a_major_the_search_lacks_commits_the_fallback_majors_search_result(page):
    # Greenhouse education "Discipline" (SpaceX, Affirm, Perpay, DV Trading, 2026-10-29): "Data Science" finds nothing,
    # the fallback search lists "Computer Science", and only the recorded major was matched against that list.
    async def no_guess(texts):
        return None

    html = markup("discipline--0", "Discipline", MAJORS, remote=True)
    got, error, _ = await pick(
        page, html, "discipline--0", "Data Science", choose=no_guess,
        search_queries=["Computer Science"], resolve=major_pick("Data Science", "Computer Science"),
    )
    assert got == "Computer Science", error
    assert await page.evaluate("window.picked") == "Computer Science"


async def test_a_fallback_major_search_never_picks_a_merely_similar_major(page):
    async def no_guess(texts):
        return None

    html = markup("discipline--0", "Discipline", MAJORS, remote=True)
    got, error, _ = await pick(
        page, html, "discipline--0", "Data Science", choose=no_guess,
        search_queries=["Statistics"], resolve=major_pick("Data Science", "Statistics"),
    )
    assert got is None and "No matching option" in error
    assert await page.evaluate("window.picked") is None
