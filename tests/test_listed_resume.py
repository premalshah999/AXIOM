"""ASSA ABLOY 2026-10-14: a resumed draft that lists our resume file counts as attached; 'No Response' does not."""

from jobpilot.answers import Resolver
from jobpilot.forms import FormSession
from jobpilot.schemas import Profile


async def verify_page(page, tmp_path, body, uploaded=False):
    resume = tmp_path / "Alex_Shah_Resume.pdf"
    resume.write_bytes(b"%PDF-1.4")
    await page.set_content(f"<html><body><label>Name<input value='Alex Doe'></label>{body}</body></html>")
    form = FormSession(page, Resolver(Profile(), None, None), resume, lambda *a: None)
    await form.scan()
    form.uploaded = uploaded  # The fill pass already set the upload input.
    return await form.verify()


async def test_listed_resume_file_is_attached(page, tmp_path):
    report = await verify_page(page, tmp_path, "<h3>CV/Resume</h3><div>Alex_Shah_Resume.pdf</div>")
    assert report["resume_attached"] is True


async def test_no_response_resume_is_not_attached(page, tmp_path):
    report = await verify_page(page, tmp_path, "<h3>Resume/CV/Cover Letter</h3><div>No Response</div>")
    assert report["resume_attached"] is False


async def test_saved_resume_preselected_in_a_picker_is_attached(page, tmp_path):
    # Starbucks (Eightfold, signed in): a resume combobox holds the saved file; the upload input is empty.
    picker = (
        "<div>Resume <input role=combobox aria-label='Resume' value='Alex_Shah_Resume.pdf'>"
        "<button>Preview</button><button>Upload new</button>"
        "<input type=file aria-label='Resume' style='display:none'></div>"
    )
    report = await verify_page(page, tmp_path, picker, uploaded=True)
    assert report["resume_attached"] is True
    other = picker.replace("Alex_Shah_Resume.pdf", "Old_CV_2023.pdf")
    assert (await verify_page(page, tmp_path, other, uploaded=True))["resume_attached"] is False
