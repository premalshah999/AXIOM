"""Allen Institute (ClearCompany) 2026-10-28: two uploads whose tables share the header "File name Date submitted" and
whose purpose is printed as plain text above each. The resume was never attached ("Resume attachment was not accepted")."""

from jobpilot.answers import Resolver
from jobpilot.forms import FormSession
from jobpilot.schemas import Profile

TABLE = "<table><thead><tr><th>File name</th><th>Date submitted</th></tr></thead></table>"
PAGE = f"""<html><body><form>
<p>Upload your most recent Resume.</p><p>*Do Not Remove previous resumes unless you would like to replace them!</p>
{TABLE}<div class=dropdown><button type=button>Upload</button><input type=file name='files[]' class=file-upload></div>
<h4>LinkedIn Profile</h4>{TABLE}
<p style='color:red'>Please upload your Cover Letter or Personal Letter as requested in the job description. *</p>
{TABLE}<div class=dropdown><button type=button>Upload</button><input type=file name='files[]' class=file-upload></div>
</form></body></html>"""


async def scanned(page, tmp_path, html=PAGE):
    resume = tmp_path / "Alex_Doe_Resume.pdf"
    resume.write_bytes(b"%PDF-1.4")
    await page.set_content(html)
    form = FormSession(page, Resolver(Profile(), None, None), resume, lambda *a: None)
    await form.scan()
    return form


async def test_each_upload_is_named_by_the_text_above_it(page, tmp_path):
    form = await scanned(page, tmp_path)
    files = [f for f in form.fields.values() if f["type"] == "file"]
    assert [f["label"].split(" (")[0] for f in files] == ["Resume/CV", "Cover Letter"]
    assert [f["required"] for f in files][1] is True
    assert [f["id"] for f in form.resume_fields()] == [files[0]["id"]]


async def test_a_lone_upload_keeps_its_own_caption(page, tmp_path):
    html = PAGE.replace("<input type=file name='files[]' class=file-upload>", "", 1)
    form = await scanned(page, tmp_path, html)
    [only] = [f for f in form.fields.values() if f["type"] == "file"]
    assert "Cover" not in only["label"]  # Not guessed from the text above; resume_fields takes the lone upload.
    assert [f["id"] for f in form.resume_fields()] == [only["id"]]


async def test_a_named_portfolio_upload_is_not_taken_for_the_resume(page, tmp_path):
    html = (
        "<html><body><form><p>Attach your resume</p><input type=file aria-label='Resume'>"
        "<p>Resume samples are welcome below.</p><label>Portfolio <input type=file></label></form></body></html>"
    )
    form = await scanned(page, tmp_path, html)
    labels = [f["label"].split(" (")[0] for f in form.fields.values() if f["type"] == "file"]
    assert labels[0].startswith("Resume") and not labels[1].startswith("Resume")
