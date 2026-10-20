"""Qualcomm (Eightfold) 2026-10-14: a consent-only review pop-up's Submit is not the application submit."""

import pytest

from jobpilot.answers import Resolver
from jobpilot.forms import FormSession
from jobpilot.schemas import Profile

DIALOG = """<main><h1>Apply</h1><label>Phone<input id=phone></label></main>
<div role=dialog aria-modal=true><h2>Please review before continuing</h2>
  <label><input type=checkbox id=pp> I have read and agree to the Privacy Policy and the Interview Policy.</label>
  <p>Your choice will guide how we manage your data across our systems.</p>
  <div role=radiogroup aria-label="Your choice will guide how we manage your data across our systems">
    <label><input type=radio name=c value=all> Use my profile data for ANY ROLES</label>
    <label><input type=radio name=c value=one> Use my profile data for ONLY THE ROLE I AM APPLYING FOR</label></div>
  <label><input type=checkbox id=alerts> I would like to receive AI-powered job alerts</label>
  <button type=button id=go onclick="document.body.dataset.done=1">Submit</button></div>"""

PAGE = """<main><label>Phone<input id=phone></label><label>Why us?<textarea id=why></textarea></label>
  <button type=button onclick="document.body.dataset.done=1">Submit</button></main>"""


async def form_for(page, tmp_path, html):
    await page.set_content(f"<html><body>{html}</body></html>")
    form = FormSession(page, Resolver(Profile(), None, None), tmp_path / "r.pdf", lambda *a: None)
    await form.scan()
    submit = next(b for b in form.buttons.values() if b["label"].strip() == "Submit")
    return form, submit


async def test_consent_dialog_submit_is_allowed(page, tmp_path):
    form, submit = await form_for(page, tmp_path, DIALOG)
    await form.click(submit["id"])
    assert await page.evaluate("document.body.dataset.done") == "1"


async def test_application_page_submit_is_still_refused(page, tmp_path):
    form, submit = await form_for(page, tmp_path, PAGE)
    with pytest.raises(ValueError, match="Use the submit action"):
        await form.click(submit["id"])
