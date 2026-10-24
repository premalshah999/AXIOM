"""Axtria (JazzHR, 2026-10-24): a datepicker without a placeholder asks "Completion date of your highest degree
(mm/yyyy)". It was read as month/day/year, the profile's "2026-05" did not fit, and the question went to the owner."""

from jobpilot.answers import Resolver
from jobpilot.forms import FormSession
from jobpilot.schemas import Profile

PAGE = """<form><label for=d>Completion date of your highest degree (mm/yyyy)</label>
  <input id=d type=text class="form-control datepicker"></form>"""


async def test_a_month_year_question_on_a_datepicker_is_answered_and_typed_as_month_year(page, service, tmp_path):
    await page.set_content(PAGE)
    profile = Profile.model_validate(
        {"education": [{"school": "State University", "degree": "Master of Science", "end": "2026-05"}]}
    )
    resolver = Resolver(profile, service.db, service.config, job={"ats": "jazzhr", "url": "https://example.test/job"})
    form = FormSession(page, resolver, tmp_path / "unused.pdf", lambda *a: None)
    field = next(f for f in (await form.scan())["fields"] if f["label"].startswith("Completion date"))
    assert field["widget"] == "date-text" and field["date_format"] == "month-year"
    answer = resolver.local(field)
    assert answer.disposition == "answer" and answer.evidence_ids[0] == "profile:education.0.end"
    await form.fill(field, answer.value)
    assert await page.locator("#d").input_value() == "05/2026"
