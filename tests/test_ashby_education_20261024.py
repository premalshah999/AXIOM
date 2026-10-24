"""Quora's Ashby application (2026-10-24, checked live with REA): its School results show the name, the country and
the web domain, and the website commits the name alone, so a committed school never matched its option; and the
graduation date box refused the owner's own month/year reply as too imprecise."""

from jobpilot.answers import Resolver
from jobpilot.forms import FormSession
from jobpilot.schemas import Profile
from jobpilot.widgets import visible_options

OPTION = """<div role=listbox><div role=option class="ashby-application-form-input-autocomplete-popup-result">
  <div><div><span class="_canonicalSchoolResultName_1e3gg_205">State University, North Campus</span>
  <span class="_canonicalSchoolResultCountry_1e3gg_209">United States</span></div>
  <span class="_canonicalSchoolResultDomain_1e3gg_218">state.edu</span></div></div></div>"""


async def test_a_school_result_is_read_by_its_name(page):
    await page.set_content(OPTION)
    assert [o["text"] for o in await visible_options(page.main_frame)] == ["State University, North Campus"]


async def test_a_month_year_answer_fills_a_full_date_box_with_the_first(page, service, tmp_path):
    await page.set_content("""<form><label for=g>Graduation Date</label>
      <input id=g type=text class="ashby-application-form-input-date" placeholder="MM/DD/YYYY"></form>""")
    profile = Profile.model_validate({"answer_rules": {"history_day": "first"}})
    form = FormSession(page, Resolver(profile, service.db, service.config), tmp_path / "unused.pdf", lambda *a: None)
    field = next(f for f in (await form.scan())["fields"] if f["label"] == "Graduation Date")
    assert field["widget"] == "date-text"
    await form.fill(field, "05/2026")
    assert await page.locator("#g").input_value() == "05/01/2026"


async def test_without_the_owners_rule_a_month_is_never_widened_to_a_day(page, service, tmp_path):
    import pytest

    await page.set_content("""<form><label for=g>Graduation Date</label>
      <input id=g type=text placeholder="MM/DD/YYYY"></form>""")
    form = FormSession(page, Resolver(Profile(), service.db, service.config), tmp_path / "unused.pdf", lambda *a: None)
    field = next(f for f in (await form.scan())["fields"] if f["label"] == "Graduation Date")
    with pytest.raises(ValueError, match="precision"):
        await form.fill(field, "05/2026")
