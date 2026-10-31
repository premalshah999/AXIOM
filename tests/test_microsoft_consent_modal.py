"""Microsoft (Eightfold PCSX) 2026-10-27: "Please review before continuing" asks for a country, the applicant's choice on
AI-insights visibility and two communication boxes before the sign-up. Its plain "Submit" was read as the application's
final submit ("needs a resume attachment") because the country picker, the radio group's label and the radio options named
none of the consent words; eight jobs parked on it across five runs each."""

import pytest

from jobpilot.answers import Resolver
from jobpilot.forms import FormSession
from jobpilot.schemas import Profile

MODAL = """<main><h1>Software Engineer</h1><p>Job description</p></main>
<div role=dialog aria-modal=true><h2>Please review before continuing</h2>
  <p>Your process starts here when you sign-up and create a new account to help you stay connected.</p>
  <label for=cc>Country/region of residence *</label>
  <select id=cc><option value="">Select a country</option><option>United States</option></select>
  <div role=radiogroup aria-label="AI-insights visibility to Microsoft Recruiting">
    <label><input type=radio name=v value=y> Yes, allow Microsoft Recruiting to see my profile as a potential match recommendation for roles I haven't applied for, based on AI insights.</label>
    <label><input type=radio name=v value=n> No, do not allow Microsoft Recruiting to see my profile as a potential match recommendation for roles I haven't applied for, based on AI insights.</label></div>
  <label><input type=checkbox id=c1> Yes, I would like to receive electronic communications from Microsoft Recruiting.</label>
  <label><input type=checkbox id=c2> Yes, I would like to receive email notifications when new jobs open that match my profile.</label>
  <button type=button id=go onclick="document.body.dataset.done=1">Submit</button></div>"""

APPLICATION = """<main><h1>Apply</h1><label for=cc>Country/region of residence</label><select id=cc><option>United States</option></select>
  <div role=radiogroup aria-label="Are you authorized to work in the US?"><label><input type=radio name=a value=y> Yes</label><label><input type=radio name=a value=n> No</label></div>
  <label>Why us?<textarea id=why></textarea></label>
  <button type=button onclick="document.body.dataset.done=1">Submit</button></main>"""


async def submit_of(page, tmp_path, html):
    await page.set_content(f"<html><body>{html}</body></html>")
    form = FormSession(page, Resolver(Profile(), None, None), tmp_path / "r.pdf", lambda *a, **k: None)
    obs = await form.scan()
    return form, obs, next(b for b in obs["controls"] if b["label"].strip() == "Submit")


async def test_the_country_and_preference_modal_submit_is_not_the_application(page, tmp_path):
    form, obs, submit = await submit_of(page, tmp_path, MODAL)
    assert await form.intermediate_submit(submit["id"], obs)
    await form.click(submit["id"])
    assert await page.evaluate("document.body.dataset.done") == "1"


async def test_an_application_page_with_a_country_and_questions_is_still_final(page, tmp_path):
    form, obs, submit = await submit_of(page, tmp_path, APPLICATION)
    assert not await form.intermediate_submit(submit["id"], obs)
    with pytest.raises(ValueError, match="Use the submit action"):
        await form.click(submit["id"])
