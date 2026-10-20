"""The SuccessFactors overwrite-prefilled-data confirmation is cancelled at verify; other confirmations are untouched."""

from jobpilot.forms import FormSession

OVERWRITE = """<section class="sapMDialog" id=dlg><header>Confirmation</header>
  <div class=content><span>Uploading your resume again will overwrite the prefilled data in your application. Do you wish to proceed?</span></div>
  <footer><button id=yes onclick="document.title='yes'">Yes</button>
  <button id=no onclick="document.getElementById('dlg').remove();document.title='cancelled'"><bdi>Cancel</bdi></button></footer></section>"""

SUBMIT = """<div role=dialog aria-modal=true id=dlg><p>Are you sure you want to submit your application?</p>
  <button id=yes onclick="document.title='submitted'">Yes</button>
  <button id=no onclick="document.getElementById('dlg').remove();document.title='kept'">No</button></div>"""

WITHDRAW = """<div role=dialog aria-modal=true id=dlg><p>Withdraw this application? This cannot be undone.</p>
  <button id=yes onclick="document.title='withdrawn'">Yes</button>
  <button id=no onclick="document.getElementById('dlg').remove();document.title='kept'">Cancel</button></div>"""


async def _verify(page, tmp_path):
    form = FormSession(page, None, tmp_path / "resume.pdf", lambda *a, **k: None)
    await form.scan()
    return await form.verify()


async def test_verify_cancels_the_overwrite_dialog_and_never_clicks_yes(page, tmp_path):
    await page.set_content(OVERWRITE)
    await _verify(page, tmp_path)
    assert await page.title() == "cancelled"
    assert await page.locator("#dlg").count() == 0


async def test_verify_leaves_a_submit_confirmation_alone(page, tmp_path):
    await page.set_content(SUBMIT)
    await _verify(page, tmp_path)
    assert await page.title() == ""
    assert await page.locator("#dlg").count() == 1


async def test_verify_leaves_a_withdraw_confirmation_alone(page, tmp_path):
    await page.set_content(WITHDRAW)
    await _verify(page, tmp_path)
    assert await page.title() == ""
    assert await page.locator("#dlg").count() == 1
