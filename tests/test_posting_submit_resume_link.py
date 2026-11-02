"""Apple 2026-10-27: the posting page's "Submit Resume" is a link to the application, and the final-submit guard refused
it ("Use the submit action") six decisions in a row until the recovery budget ended."""

import pytest

from jobpilot.answers import Resolver
from jobpilot.forms import FormSession
from jobpilot.schemas import Profile

POSTING = """<main><h1>Graphics Engineer</h1><p>Job description</p>
<a href="https://jobs.example.test/apply/1" role=link id=entry onclick="document.body.dataset.opened=1; event.preventDefault()">Submit Resume</a></main>"""
# A review step that shows the answers as text and submits with a link to nowhere: the commit's, never the way in.
REVIEW = """<main><h1>Review</h1><p>Email: alex@example.test</p>
<a href="{href}" role=link onclick="document.body.dataset.done=1; event.preventDefault()">Submit Resume</a></main>"""
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


@pytest.mark.parametrize("href", ["#", "#submit", "javascript:void(0)", "javascript:__doPostBack('submit','')", ""])
async def test_a_review_steps_submit_resume_link_is_still_final(page, tmp_path, href):
    form, link = await button_for(page, tmp_path, REVIEW.format(href=href))
    with pytest.raises(ValueError, match="Use the submit action"):
        await form.click(link["id"])
    assert await page.evaluate("document.body.dataset.done") is None


async def test_after_anything_was_written_a_submit_resume_link_is_final(page, tmp_path):
    form, link = await button_for(page, tmp_path, POSTING)
    form.wrote_this_run = True  # An earlier document of this run was filled: its ledger was started again for this one.
    with pytest.raises(ValueError, match="Use the submit action"):
        await form.click(link["id"])
    assert await page.evaluate("document.body.dataset.opened") is None


def test_only_a_real_address_to_another_page_opens_one():
    from jobpilot.forms import opens_another_page

    here = "https://jobs.example.test/job/7/review"
    assert opens_another_page("/apply/7", here) and opens_another_page("https://apply.example.test/x", here)
    for href in ("", "#", "#go", "javascript:void(0)", "mailto:a@example.test", "/job/7/review", "/job/7/review/", "?step=2"):
        assert not opens_another_page(href, here), href


async def test_a_submit_resume_button_over_questions_is_still_final(page, tmp_path):
    form, button = await button_for(page, tmp_path, FORM)
    with pytest.raises(ValueError, match="Use the submit action"):
        await form.click(button["id"])
