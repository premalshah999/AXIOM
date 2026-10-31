"""Apple 2026-10-27: the posting page's "Submit Resume" is a link to the application, and the final-submit guard refused
it ("Use the submit action") six decisions in a row until the recovery budget ended."""

import pytest

from jobpilot.answers import Resolver
from jobpilot.forms import FormSession
from jobpilot.schemas import Profile

POSTING = """<main><h1>Graphics Engineer</h1><p>Job description</p>
<a href="#apply" role=link id=entry onclick="document.body.dataset.opened=1">Submit Resume</a></main>"""
FORM = """<main><h1>Apply</h1><label>Why us?<textarea id=why></textarea></label>
<button type=button onclick="document.body.dataset.done=1">Submit Resume</button></main>"""


async def button_for(page, tmp_path, html):
    await page.set_content(f"<html><body>{html}</body></html>")
    form = FormSession(page, Resolver(Profile(), None, None), tmp_path / "r.pdf", lambda *a, **k: None)
    await form.scan()
    return form, next(b for b in form.buttons.values() if b["label"].strip() == "Submit Resume")


async def test_a_posting_pages_submit_resume_link_opens_the_application(page, tmp_path):
    form, link = await button_for(page, tmp_path, POSTING)
    await form.click(link["id"])
    assert await page.evaluate("document.body.dataset.opened") == "1"


async def test_a_submit_resume_button_over_questions_is_still_final(page, tmp_path):
    form, button = await button_for(page, tmp_path, FORM)
    with pytest.raises(ValueError, match="Use the submit action"):
        await form.click(button["id"])
