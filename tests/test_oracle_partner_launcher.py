"""Mortenson (Oracle, 2026-10-27): the Tax Credits block's "Take Tax Credit Assessment" was never taken. The readback
held only fields and passed, Oracle refused the submit with "This info is required. (1 issue)", and the refused page
left the launcher without text, so the navigator could not find it. The launcher is a required step while shown."""

from jobpilot.forms import SCAN

PAGE = """<form>
<div class="apply-flow-block apply-flow-block--inline-tax-credits">
  <h3 class="apply-flow-block__title"><span>Tax Credits</span></h3>
  <p class="apply-flow-block__instructions">Please click the TAKE TAX CREDIT ASSESSMENT button.</p>
  <div class="apply-flow-block__form-list" role="region" aria-label="Tax Credits ">
    <div class="inline-partner" tabindex="-1">
      <button id=launch class="button standard-apply-flow__partner-button"><span class=inline-spinner>
        <span class=inline-spinner__label>%s</span></span></button>
    </div>
  </div>
</div>
<button type=submit>Submit</button>
</form>"""


async def test_the_partner_launcher_is_flagged_and_named(page):
    for text, label in [("Take Tax Credit Assessment", "Take Tax Credit Assessment"), ("", "Tax Credits")]:
        await page.set_content(PAGE % text)
        data = await page.main_frame.evaluate(SCAN)
        launcher = next(b for b in data["buttons"] if b.get("partner"))
        assert launcher["label"] == label
        assert not next(b for b in data["buttons"] if b["label"] == "Submit").get("partner")


async def test_a_shown_launcher_fails_the_readback(page, tmp_path):
    from jobpilot.answers import Resolver
    from jobpilot.forms import FormSession
    from jobpilot.schemas import Profile

    resume = tmp_path / "r.pdf"
    resume.write_bytes(b"%PDF")
    await page.set_content(PAGE % "Take Tax Credit Assessment")
    form = FormSession(page, Resolver(Profile(), None, None), resume, lambda *a, **k: None)
    report = await form.verify()
    assert "Required step incomplete: Take Tax Credit Assessment" in report["problems"]
    await page.evaluate("document.querySelector('.inline-partner').innerHTML = '<p>Completed</p>'")
    report = await form.verify()
    assert not [p for p in report["problems"] if p.startswith("Required step incomplete")]
