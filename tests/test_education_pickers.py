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


async def test_the_pages_list_in_the_tenants_wording_decides_without_probe_searches(page, service, tmp_path):
    from jobpilot.forms import FormSession

    await open_prompt(page, ["Accounting", "Biology", "Computer and Information Science", "Mathematics"])
    form = FormSession(page, Resolver(Profile.model_validate(PROFILE), service.db, service.config),
                       tmp_path / "unused.pdf", lambda *a: None)
    form.network = SimpleNamespace(
        value_list=lambda kind: ["Accounting", "Biology", "Computer and Information Science", "Mathematics"],
        quiet=lambda *_: True,
    )
    field = next(f for f in (await form.scan())["fields"] if f["label"] == "Field of Study")
    field["group"] = "Education 1"
    started = time.monotonic()
    assert await form.fill(field, "Marine Biology") == "Computer and Information Science"
    # Only the chosen name was searched: the recorded major and its first word were never probed.
    assert await page.evaluate("window.SEARCHES") == ["Computer and Information Science"]
    assert time.monotonic() - started < 4


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


def test_a_dial_code_is_stripped_in_every_spelling_a_site_uses():
    for text in ("United States (1)", "(+1) United States", "+1 (United States)", "United States +1"):
        assert widgets.dial_name(text) == "United States"
    assert widgets.dial_name("United States of America (+1)") == "United States of America"
    # A bare number is part of a name, not a code.
    assert widgets.dial_name("2 Year Degree") == "2 Year Degree"


def test_the_saved_code_and_the_row_the_page_holds_name_one_country():
    assert widgets.same_dial_country("United States (1)", "+1 (United States of America)")
    assert widgets.same_dial_country("United States (1)", "United States of America (+1)")
    assert not widgets.same_dial_country("United States (1)", "+44 (United Kingdom)")
    assert not widgets.same_dial_country("Canada (1)", "+1 (United States)")


def test_the_rescan_reads_a_phone_code_row_as_the_saved_code_in_any_spelling():
    # iCIMS shows the committed row "United States (1)"; the re-scan compared it as text to "+1" and to
    # "+1 (United States of America)" and called the field unset (54 stalled runs, 2026-10-24..31).
    field = {"type": "combobox", "label": "Phone Country Code"}
    for saved in ("+1", "+1 (United States of America)", "United States of America (+1)", "United States"):
        assert widgets.same_value(field, "United States (1)", saved), saved
    for saved in ("+44", "+44 (United Kingdom)"):
        assert not widgets.same_value(field, "United States (1)", saved)
    assert not widgets.same_value(field, "Canada (1)", "+1")  # A shared code is not a shared country.
