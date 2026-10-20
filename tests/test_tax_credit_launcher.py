"""Oracle's required "Take Tax Credit Assessment" is a section launcher, not the final submit (Caesars)."""

import pytest

from jobpilot.answers import Resolver
from jobpilot.forms import FormSession
from jobpilot.browser import GUARD
from jobpilot.schemas import Profile

PAGE = """<form onsubmit="event.preventDefault(); document.title='clicked:' + event.submitter.textContent">
<label>Tax Credits<input name="tc"></label>
<button type="submit">Take Tax Credit Assessment</button>
<button type="submit">Send</button></form>"""


async def test_tax_credit_launcher_is_clickable_but_a_generic_submit_is_not(page, tmp_path):
    await page.set_content(PAGE)
    form = FormSession(page, Resolver(Profile(), None, None), tmp_path / "resume.pdf", lambda *a: None)
    await form.scan()
    by_label = {b["label"]: b["id"] for b in form.buttons.values()}
    await form.click(by_label["Take Tax Credit Assessment"])
    assert await page.title() == "clicked:Take Tax Credit Assessment"
    with pytest.raises(ValueError, match="may submit the application"):
        await form.click(by_label["Send"])


async def test_create_profile_popup_submit_is_a_step_not_the_final_submit(page, tmp_path):
    await page.set_content("""
      <form><label>Resume<input type=file name=resume></label><label>Why us?<textarea name=w></textarea></label>
      <button type=submit>Submit application</button></form>
      <div role=dialog aria-modal=true><h2>Create your profile</h2>
        <label>First Name<input name=fn value=A></label><label>Last Name<input name=ln value=B></label>
        <label>Phone Number<input type=tel name=p value=1></label><label>Location<input name=loc value=C></label>
        <button type=button onclick="document.title='profile saved'">Submit</button></div>""")
    await page.evaluate(GUARD)  # the app's page-side guard that cancels unmarked "Submit" clicks
    form = FormSession(page, Resolver(Profile(), None, None), tmp_path / "resume.pdf", lambda *a: None)
    await form.scan()
    # The scan sees only the open modal: its fields (incl. Location) and its own Submit.
    popup_submit = next(b["id"] for b in form.buttons.values() if b["label"] == "Submit")
    await form.click(popup_submit)
    assert await page.title() == "profile saved"


async def test_submit_on_a_non_account_popup_stays_guarded(page, tmp_path):
    await page.set_content("""<div role=dialog aria-modal=true><h2>Review your application</h2>
        <label>Why us?<textarea name=w>x</textarea></label>
        <button type=button onclick="document.title='sent'">Submit</button></div>""")
    form = FormSession(page, Resolver(Profile(), None, None), tmp_path / "resume.pdf", lambda *a: None)
    await form.scan()
    with pytest.raises(ValueError):
        await form.click(next(b["id"] for b in form.buttons.values() if b["label"] == "Submit"))
    assert await page.title() != "sent"


WOTC_PAGE = """<form onsubmit="event.preventDefault()">
  <h3>Work Opportunity Tax Credit (WOTC)*</h3>
  <button type=submit onclick="openSurvey()">Complete WOTC</button>
  <button type=submit>Submit application</button></form>
<script>
function openSurvey() {
  const w = window.open('about:blank');
  w.document.write('<h1>WOTC Survey</h1><label>Zip code<input name=zip></label>'
    + '<button type=button onclick="document.body.innerHTML=\\'<h1>Thank you, your survey is complete</h1>\\'">Finish</button>');
  w.document.close();
}
</script>"""


async def test_wotc_launcher_adopts_its_survey_tab_and_returns_when_done(page, tmp_path):
    # Starbucks (Eightfold) 2026-10-14: "Complete WOTC" is a submit-type button opening a survey tab.
    await page.set_content(WOTC_PAGE)
    form = FormSession(page, Resolver(Profile(), None, None), tmp_path / "resume.pdf", lambda *a: None)
    await form.scan()
    by_label = {b["label"]: b["id"] for b in form.buttons.values()}
    after = await form.click(by_label["Complete WOTC"])
    assert form.page is not page and "Zip code" in [f["label"] for f in after["fields"]]
    finish = next(b["id"] for b in form.buttons.values() if b["label"] == "Finish")
    after = await form.click(finish)
    assert form.page is page and "Submit application" in [b["label"] for b in after["controls"]]


async def test_profile_overlay_without_dialog_role_is_a_step(page, tmp_path):
    # Qualcomm (Eightfold) 2026-10-15: a "Create your profile" overlay with no role=dialog and no heading.
    # Long application text before the overlay pushes its heading out of the scan's text window.
    await page.set_content(
        """<nav>"""
        + "Jobs Dashboard Profile Culture Benefits " * 40
        + """</nav>
      <main>"""
        + "<p>Job description paragraph about the role and the team.</p>" * 200
        + """</main>
      <div class=overlay><p class=title>Create your profile</p>
      <p>Required fields are marked with *</p>
      <label>First Name<input name=fn value=A></label><label>Last Name<input name=ln value=B></label>
      <label>Country code<select name=cc><option>(+1) United States of America</option></select></label>
      <label>Phone Number<input type=text name=p value="(555) 555-0100"></label>
      <button type=submit onclick="event.preventDefault(); document.title='profile saved'">Submit</button></div>"""
    )
    await page.evaluate(GUARD)
    form = FormSession(page, Resolver(Profile(), None, None), tmp_path / "resume.pdf", lambda *a: None)
    await form.scan()
    await form.click(next(b["id"] for b in form.buttons.values() if b["label"] == "Submit"))
    assert await page.title() == "profile saved"


async def test_profile_dialog_with_optional_resume_alternative_is_a_step(page, tmp_path):
    # Qualcomm (Eightfold) 2026-10-15: the "Create your profile" modal keeps a hidden optional resume input.
    await page.set_content("""<div role=dialog aria-modal=true><h2>Create your profile</h2>
      <label>Upload your resume<input type=file name=r></label>
      <label>First Name<input name=fn value=A></label><label>Last Name<input name=ln value=B></label>
      <label>Phone Number<input type=tel name=p value=1></label>
      <button type=submit onclick="event.preventDefault(); document.title='profile saved'">Submit</button></div>""")
    await page.evaluate(GUARD)
    form = FormSession(page, Resolver(Profile(), None, None), tmp_path / "resume.pdf", lambda *a: None)
    await form.scan()
    await form.click(next(b["id"] for b in form.buttons.values() if b["label"] == "Submit"))
    assert await page.title() == "profile saved"


async def test_application_page_with_resume_upload_stays_guarded(page, tmp_path):
    # Not a dialog: a page with name/phone and a resume upload is an application, so Submit stays guarded.
    await page.set_content("""<h2>Create your profile and apply</h2>
      <label>Resume<input type=file name=r></label><label>First Name<input name=fn value=A></label>
      <button type=submit onclick="event.preventDefault(); document.title='sent'">Submit</button>""")
    form = FormSession(page, Resolver(Profile(), None, None), tmp_path / "resume.pdf", lambda *a: None)
    await form.scan()
    with pytest.raises(ValueError):
        await form.click(next(b["id"] for b in form.buttons.values() if b["label"] == "Submit"))
    assert await page.title() != "sent"
