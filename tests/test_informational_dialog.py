"""ADP's tax-credit questionnaire (Terminix via Workday, 2026-10-23) opens with "Quick tip before you begin ... Okay"
over its questions; the page hides them behind the dialog, the scan found no fields and the run stalled twice."""

from jobpilot.answers import Resolver
from jobpilot.forms import FormSession
from jobpilot.schemas import Profile

PAGE = """<main id=main aria-hidden=true>
  <fieldset><legend>Did you receive Supplemental Security Income (SSI) in the past 60 days?</legend>
  <label><input type=radio name=ssi value=yes>Yes</label><label><input type=radio name=ssi value=no>No</label></fieldset>
</main>
<div role=dialog aria-modal=true id=tip style="width:400px"><h2>Quick tip before you begin</h2>
  <p>Not sure about your answers? Select "Yes" and we'll verify the information on your behalf.</p>
  <button aria-label="Close dialog" onclick="close_()">×</button><button onclick="close_()">Okay</button></div>
<script>function close_(){document.getElementById('tip').remove();document.getElementById('main').removeAttribute('aria-hidden');}</script>"""


async def test_an_informational_dialog_is_acknowledged_and_the_form_scanned(page, tmp_path):
    await page.set_content(PAGE)
    form = FormSession(page, Resolver(Profile(), None, None), tmp_path / "r.pdf", lambda *a, **k: None)
    obs = await form.scan()
    assert await page.locator("#tip").count() == 0
    assert any("Supplemental Security Income" in f["label"] for f in obs["fields"])


async def test_a_consequential_dialog_is_never_acknowledged(page, tmp_path):
    await page.set_content(PAGE.replace("Quick tip before you begin", "Are you sure you want to withdraw your application?"))
    form = FormSession(page, Resolver(Profile(), None, None), tmp_path / "r.pdf", lambda *a, **k: None)
    await form.scan()
    assert await page.locator("#tip").count() == 1
