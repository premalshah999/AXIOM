"""Jabil's Workday My Experience (2026-10-23) has two inputs labelled "Upload a file (5MB max)": one under
"Resume/CV/Cover Letter/Supporting Documents", one under "Additional Attachments". With two unnamed file inputs no
resume was attached, and Review showed "Resume/CV ... No Response" until the run stalled."""

from jobpilot.answers import Resolver
from jobpilot.forms import FormSession
from jobpilot.schemas import Profile

SECTION = """<div role="group" aria-labelledby="{id}-section"><h4 id="{id}-section">{title}</h4>
  <div data-fkit-id="{key}--null"><p><div data-automation-id="formField-" data-fkit-id="{key}--attachments">
  <label id="l{key}">Upload a file (5MB max)</label><div><div style="width: 100%">
  <div data-automation-id="attachments-FileUpload" aria-labelledby="l{key}"><div>
  <div data-automation-id="file-upload-drop-zone"><div>Drop files here</div><div><div>or</div>
  <button type=button data-automation-id=select-files id="{key}--attachments">Select files</button></div></div>
  <input data-automation-id="file-upload-input-ref" type=file multiple data-slot="{n}"></div></div></div></div></div></p></div></div>"""


async def test_the_upload_under_the_resume_section_is_the_resume(page, tmp_path):
    resume = tmp_path / "resume.pdf"
    resume.write_bytes(b"%PDF-1.4 test")
    await page.set_content(
        "<form>"
        + SECTION.format(id="Resume/CV/Cover-Letter/Supporting-Documents", key="resumeAttachments", n=1, title="Resume/CV/Cover Letter/Supporting Documents")
        + SECTION.format(id="Additional-Attachments", key="additionalAttachments", n=2, title="Additional Attachments")
        + "</form>"
    )
    form = FormSession(page, Resolver(Profile(), None, None), resume, lambda *a: None)
    await form.scan()
    chosen = form.resume_fields()
    assert len(chosen) == 1
    assert await form.locator(chosen[0]["id"]).evaluate("e => e.dataset.slot") == "1"


async def test_two_unnamed_uploads_without_sections_stay_ambiguous(page, tmp_path):
    resume = tmp_path / "resume.pdf"
    resume.write_bytes(b"%PDF-1.4 test")
    await page.set_content("<form><input type=file id=a><input type=file id=b></form>")
    form = FormSession(page, Resolver(Profile(), None, None), resume, lambda *a: None)
    await form.scan()
    assert form.resume_fields() == []
