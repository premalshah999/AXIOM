"""Gem (Nuvo, 2026-10-31): the form ends in "Apply and save" and "Apply without saving". Neither names a submit, so the run
saw "No final submit control" on a finished form. The pair is the application's own submit, and the applicant's profile is
not Gem's to keep."""

from jobpilot.answers import Resolver
from jobpilot.forms import FormSession
from jobpilot.schemas import Profile

# The real page has no <form>: its fields and both buttons sit in plain divs.
PAGE = """<main><div id=app><label>Name <input name=name></label>
  <label>Resume <input type=file name=resume></label>
  <button type=submit id=keep>Apply and save</button><button type=submit id=plain>Apply without saving</button></div></main>"""


async def finals(page, tmp_path, html, uploaded=True):
    resume = tmp_path / "resume.pdf"
    resume.write_bytes(b"%PDF-1.4 test")
    await page.set_content(html)
    form = FormSession(page, Resolver(Profile(), None, None), resume, lambda *a, **k: None)
    await form.scan()
    form.uploaded = form.upload_verified = uploaded
    return form, await form.application_submit_controls()


async def test_the_pair_resolves_to_apply_without_saving(page, tmp_path):
    form, final = await finals(page, tmp_path, PAGE)
    assert len(final) == 1
    assert await form.locator(final[0]["id"]).evaluate("b=>b.id") == "plain"


async def test_apply_and_save_alone_is_still_the_submit(page, tmp_path):
    form, final = await finals(page, tmp_path, PAGE.replace('<button type=submit id=plain>Apply without saving</button>', ""))
    assert [b["label"] for b in final] == ["Apply and save"]


async def test_the_pair_without_a_verified_resume_is_no_submit(page, tmp_path):
    form, final = await finals(page, tmp_path, "<main><h1>Role</h1><button>Apply and save</button></main>", uploaded=False)
    assert final == []
