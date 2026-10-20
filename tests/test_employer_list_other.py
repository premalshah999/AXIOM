"""Goldman Sachs' Oracle form asks "Name of Latest Employer" from a fixed company list (1-800 CONTACTS, Accenture,
Robert Half International...). The applicant's employer is not on it, and the field never committed (2026-10-18).
Only the list's own Other / not listed choice may commit; a similar name ("Robert Half" for "Robert H. Smith") never."""

from unittest.mock import AsyncMock

import pytest

from jobpilot import widgets
from jobpilot.forms import EMPLOYER_PICKER

LIST = """<div class="cx-select-container"><input id=employer role=combobox aria-controls=employer-listbox
    oninput="search(this.value)"><div role=grid id=employer-listbox class=cx-select__list hidden></div></div>
  <script>
  window.picked = null;
  const ALL = %s;
  function search(q){const m=document.getElementById('employer-listbox');m.hidden=false;
    const items = ALL.filter(t => t.toLowerCase().includes(q.toLowerCase()));
    m.innerHTML = items.map(t => '<div role=row><div role=gridcell class="cx-select__list-item" onclick="window.picked=this.textContent;document.getElementById(\\'employer\\').value=this.textContent;this.closest(\\'[role=grid]\\').hidden=true"><span>' + t + '</span></div></div>').join('');}
  </script>"""


@pytest.mark.parametrize("companies,expected", [
    (["1-800 CONTACTS", "Accenture", "Robert Half International", "Other"], "Other"),
    (["1-800 CONTACTS", "Accenture", "Robert Half International"], None),
])
async def test_an_unlisted_employer_commits_only_the_lists_other(page, companies, expected):
    import json

    await page.set_content(LIST % json.dumps(companies))
    choose = AsyncMock(return_value="Robert Half International")
    if expected:
        chosen = await widgets.combobox(page.main_frame, page.locator("#employer"),
                                        "Robert H. Smith School of Business", choose=choose, employer_scope=True)
        assert chosen == expected and await page.evaluate("window.picked") == expected
    else:
        with pytest.raises(ValueError):
            await widgets.combobox(page.main_frame, page.locator("#employer"),
                                   "Robert H. Smith School of Business", choose=choose, employer_scope=True)
        assert await page.evaluate("window.picked") is None
    choose.assert_not_called()


def test_employer_pickers_are_recognized():
    assert EMPLOYER_PICKER.search("Name of Latest Employer")
    assert EMPLOYER_PICKER.search("Current Employer")
    assert not EMPLOYER_PICKER.search("Have you ever worked for this employer?")


ORACLE_URL = "https://fixture.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX/job/1/apply/section/2"
ORACLE_LIST = """<div class="cx-select-container"><label for=employer>Name of Latest Employer</label>
  <input id=employer class=cx-select-input role=combobox aria-controls=employer-listbox aria-expanded=false
    oninput="search(this.value)"><div role=grid id=employer-listbox class=cx-select__list hidden></div></div>
  <script>
  window.picked = null;
  const ALL = %s;
  function search(q){const m=document.getElementById('employer-listbox');m.hidden=false;
    const e=document.getElementById('employer');e.setAttribute('aria-expanded','true');
    const items = q ? ALL.filter(t => t.toLowerCase().includes(q.toLowerCase())) : [];
    m.innerHTML = items.map(t => '<div role=row><div role=gridcell class="cx-select__list-item" onclick="window.picked=this.textContent;document.getElementById(\\'employer\\').value=this.textContent;this.closest(\\'[role=grid]\\').hidden=true;document.getElementById(\\'employer\\').setAttribute(\\'aria-expanded\\',\\'false\\')"><span>' + t + '</span></div></div>').join('');}
  </script>"""


async def test_oracle_driver_commits_the_lists_other_for_an_unlisted_employer(page):
    # Goldman Sachs runs through the Oracle driver (widgets_ats.select_combo), not the generic combobox.
    import json

    from jobpilot.widgets_ats import select_combo

    companies = ["1-800 CONTACTS", "Accenture", "Robert Half International"] + [f"Firm {i}" for i in range(250)] + ["Other"]
    await page.route("https://fixture.fa.us2.oraclecloud.com/**", lambda route: route.fulfill(
        body=f"<html><body>{ORACLE_LIST % json.dumps(companies)}</body></html>", content_type="text/html"))
    await page.goto(ORACLE_URL)
    field = {"id": "employer", "label": "Name of Latest Employer", "type": "combobox"}
    committed = await select_combo(page.main_frame, page.locator("#employer"), field,
                                   "Robert H. Smith School of Business", AsyncMock(return_value=None))
    assert committed == "Other"
    assert await page.evaluate("window.picked") == "Other"
