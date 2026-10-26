"""Emory's iCIMS profile asks "What is the highest level of education you have completed?" through a server-searched
icimsDropdown (2026-10-24/25: 365 field errors in 25 runs, every write ending at the 12 s field deadline). Its select
holds only "" and its rows arrive 25 per page from the server. iCIMS writes each row's aria-label through
ICIMS.escapeHtml, so "Master's Level Degree" read as "Master&#039;s Level Degree": the applicant's degree never matched a
row, the engine paged and re-searched until the deadline, and a row it did click failed read-back against the select's
unescaped text ("did not commit the observed choice"). The fixture re-implements the vendor widget's observed behaviour
(tests/fixtures/icims_ajax_dropdown.html); the first page is the catalog production read, the rest is synthetic."""

import json
import time
from pathlib import Path

import pytest

from jobpilot import widgets
from jobpilot.answers import Resolver
from jobpilot.forms import FormSession
from jobpilot.schemas import EducationEntry, Profile

FIXTURE = (Path(__file__).parent / "fixtures" / "icims_ajax_dropdown.html").read_text()
FIRST_PAGE = [  # As production read it (event 277260), unescaped.
    "2-Year College Degree", "ABD - All but Dissertation", "Associate's Degree", "Bachelor's Level Degree",
    "Business School Certificate", "Business School Work", "CRNA", "D.O., M.P.H.", "D.O.-Doctor of Osteopathic Med",
    "D.Sc. Doctor of Science", "DD/DMN - Divinity/Ministry", "DDS/DMD - Dentistry", "Doctoral Program Work",
    "Doctorate Level Degree", "DPM/DP/POD.D - Podiatry", "DPT-Doctor of Physical Therapy", "DVM-Veterinary Science",
    "EDD Doctorate", "Education Specialist", "HS Graduate or Equivalent", "JD/PhD", "Juris Doctor/Master of Arts",
    "Less Than HS Graduate", "LLD/JD", "M.B.Ch.B.",
]
CATALOG = FIRST_PAGE + [
    "M.D. - Doctor of Medicine", "Master's Level Degree", "Master's Program Work", "MD/PhD", "MPH - Public Health",
    "MSN - Nursing", "Nursing Diploma", "Pharm.D.", "PhD", "Some College", "Technical/Vocational Certificate",
]
ANCHOR = '[id="rcf2011_icimsDropdown"]'
MASTERS = "Master's Level Degree"


async def load(page, *, search=False, latency=1000):
    await page.set_content(FIXTURE)
    await page.evaluate(
        "([catalog, search, latency]) => icimsAjaxDropdown(document.getElementById('rcf2011'), catalog, {search, latency})",
        [CATALOG, search, latency],
    )


def applicant():
    return Profile(
        education=[
            EducationEntry(school="State University", degree="Master of Science", field_of_study="Data Science",
                           start="2024-08", end="2026-05"),
            EducationEntry(school="Technical College", degree="BE", field_of_study="Computer Engineering",
                           start="2020-06", end="2024-06"),
        ]
    )


async def test_rows_read_as_the_page_shows_them_not_as_their_escaped_attributes(page):
    await load(page)
    await page.locator(ANCHOR).click()
    rows = await widgets.icims_rows(page.main_frame, "rcf2011", timeout=3)
    assert [text for _, text in rows] == FIRST_PAGE


@pytest.mark.parametrize("search", [False, True])
async def test_the_highest_degree_is_picked_from_a_server_searched_list_within_the_field_budget(
    page, service, tmp_path, search
):
    await load(page, search=search)
    form = FormSession(page, Resolver(applicant(), service.db, service.config), tmp_path / "unused.pdf", lambda *a: None)
    field = next(f for f in (await form.scan())["fields"] if f["label"].startswith("What is the highest level"))
    await form.discover_options(field)
    answer = (await form.resolver.resolve([field]))[0]
    assert answer.disposition == "answer"
    started = time.monotonic()
    assert await form.fill(field, answer.value) == "Master's Level Degree"
    assert time.monotonic() - started < 10
    assert await page.evaluate(
        "(s => [s.options.length, s.options[s.selectedIndex].text])(document.getElementById('rcf2011'))"
    ) == [1, "Master's Level Degree"]
    assert await page.evaluate("window.changes") == [f"v{CATALOG.index(MASTERS)}"]


async def test_a_click_on_a_row_with_an_apostrophe_reads_back_as_committed(page):
    await load(page)

    async def choose(offered):
        return next((t for t in offered if t.startswith("Associate")), None)

    picked = await widgets.combobox(page.main_frame, page.locator(ANCHOR), "Associate Degree", choose=choose)
    assert picked == "Associate's Degree"
    assert json.loads(await page.evaluate("JSON.stringify(window.changes)")) == [f"v{CATALOG.index(picked)}"]
