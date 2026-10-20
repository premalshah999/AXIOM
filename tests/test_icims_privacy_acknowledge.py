"""iCIMS's email step submits with "I Acknowledge the Privacy Notice" beside the email and phone fields (Schwab,
2026-10-18: "This may submit the application; use submit_application"). It is a step, not the final submit."""

import pytest

from jobpilot.answers import Resolver
from jobpilot.forms import FormSession
from jobpilot.schemas import Profile

PAGE = """<main><form onsubmit="event.preventDefault();document.body.dataset.sent=1">
  <label>Email<input type=email id=email name=css_loginName></label>
  <label>Phone<input type=tel id=phone></label>
  <button type=submit id=enterEmailSubmitButton>I Acknowledge the Privacy Notice</button></form>
  <form onsubmit="event.preventDefault();document.body.dataset.final=1">
  <label>Why us?<textarea id=why></textarea></label>
  <button type=submit>Acknowledge and Submit Application</button></form></main>"""


async def test_the_privacy_acknowledgement_is_a_step_and_a_final_submit_is_not(page, tmp_path):
    await page.set_content(f"<html><body>{PAGE}</body></html>")
    form = FormSession(page, Resolver(Profile(), None, None), tmp_path / "r.pdf", lambda *a: None)
    await form.scan()
    by_label = {b["label"].strip(): b for b in form.buttons.values()}
    await form.click(by_label["I Acknowledge the Privacy Notice"]["id"])
    assert await page.evaluate("document.body.dataset.sent") == "1"
    with pytest.raises(ValueError):
        await form.click(by_label["Acknowledge and Submit Application"]["id"])
    assert await page.evaluate("document.body.dataset.final") is None
