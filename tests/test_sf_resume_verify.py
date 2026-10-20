"""A SuccessFactors resume Browse control with nothing attached must not verify as ok."""

from jobpilot.forms import FormSession

GETTING_STARTED = """<h1>Getting Started</h1>
<p>Upload your CV/Resume so we can prefill your profile.</p>
<div class=attachmentField><span>Resume/CV</span><button id=browse>Browse</button></div>
<button id=next disabled>Next</button>"""


async def _verify(page, tmp_path):
    form = FormSession(page, None, tmp_path / "resume.pdf", lambda *a, **k: None)
    await form.scan()
    return await form.verify()


async def test_visible_resume_browse_with_nothing_attached_is_a_problem(page, tmp_path):
    await page.set_content(GETTING_STARTED)
    report = await _verify(page, tmp_path)
    assert report["ok"] is False
    assert "Resume attachment was not accepted" in report["problems"]
    assert report["resume_attached"] is False


async def test_attached_resume_tile_is_not_a_browse_problem(page, tmp_path):
    await page.set_content(GETTING_STARTED + "<p>tile: resume.pdf attached</p>")
    report = await _verify(page, tmp_path)
    assert "Resume attachment was not accepted" not in report["problems"]


async def test_page_without_a_resume_browse_control_is_not_a_browse_problem(page, tmp_path):
    await page.set_content("<h1>Review</h1><p>Your resume was reviewed.</p><button id=next>Next</button>")
    report = await _verify(page, tmp_path)
    assert "Resume attachment was not accepted" not in report["problems"]
