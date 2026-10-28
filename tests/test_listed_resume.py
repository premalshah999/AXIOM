"""ASSA ABLOY 2026-10-14: a resumed draft that lists our resume file counts as attached; 'No Response' does not."""

from jobpilot.answers import Resolver
from jobpilot.forms import FormSession
from jobpilot.schemas import Profile


async def verify_page(page, tmp_path, body, uploaded=False):
    resume = tmp_path / "Alex_Doe_Resume.pdf"
    resume.write_bytes(b"%PDF-1.4")
    await page.set_content(f"<html><body><label>Name<input value='Alex Doe'></label>{body}</body></html>")
    form = FormSession(page, Resolver(Profile(), None, None), resume, lambda *a: None)
    await form.scan()
    form.uploaded = uploaded  # The fill pass already set the upload input.
    return await form.verify()


async def test_listed_resume_file_is_attached(page, tmp_path):
    report = await verify_page(page, tmp_path, "<h3>CV/Resume</h3><div>Alex_Doe_Resume.pdf</div>")
    assert report["resume_attached"] is True


async def test_no_response_resume_is_not_attached(page, tmp_path):
    report = await verify_page(page, tmp_path, "<h3>Resume/CV/Cover Letter</h3><div>No Response</div>")
    assert report["resume_attached"] is False


async def test_saved_resume_preselected_in_a_picker_is_attached(page, tmp_path):
    # Starbucks (Eightfold, signed in): a resume combobox holds the saved file; the upload input is empty.
    picker = (
        "<div>Resume <input role=combobox aria-label='Resume' value='Alex_Doe_Resume.pdf'>"
        "<button>Preview</button><button>Upload new</button>"
        "<input type=file aria-label='Resume' style='display:none'></div>"
    )
    report = await verify_page(page, tmp_path, picker, uploaded=True)
    assert report["resume_attached"] is True
    other = picker.replace("Alex_Doe_Resume.pdf", "Old_CV_2023.pdf")
    assert (await verify_page(page, tmp_path, other, uploaded=True))["resume_attached"] is False


ICIMS_PAGE = (
    "<h3>Build your profile</h3><p>Or please select your resume from one of the following:</p>"
    "<div><i>{name}</i> <button>Replace Resume</button><input type=file aria-label='Resume' style='display:none'></div>"
)


async def icims_verify(page, tmp_path, name, uploaded=True, url="https://careers-acme.icims.com/jobs/5186/engineer/candidate"):
    resume = tmp_path / "Alex_Doe_Resume.pdf"
    resume.write_bytes(b"%PDF-1.4")
    html = f"<html><body><label>Name<input value='Alex Doe'></label>{ICIMS_PAGE.format(name=name)}</body></html>"

    async def serve(route):
        await route.fulfill(body=html, content_type="text/html")

    await page.route(url, serve)
    await page.goto(url)
    form = FormSession(page, Resolver(Profile(), None, None), resume, lambda *a: None)
    await form.scan()
    form.uploaded = uploaded
    return await form.verify()


async def test_icims_lists_our_upload_under_its_own_name(page, tmp_path):
    """Iridium 2026-10-21: iCIMS lists the uploaded resume as "Resume<yyyyMMddHHmm>.pdf". Read as not accepted, it was
    uploaded again; each upload reloaded the page and cleared the passwords, so the profile was refused every time."""
    assert (await icims_verify(page, tmp_path, "Resume202610211221.pdf"))["resume_attached"] is True


async def test_the_icims_rename_counts_only_after_this_runs_own_upload(page, tmp_path):
    assert (await icims_verify(page, tmp_path, "Resume202610211221.pdf", uploaded=False))["resume_attached"] is False
    assert (await icims_verify(page, tmp_path, "Old_CV_2023.pdf"))["resume_attached"] is False


async def test_the_icims_rename_is_never_uploaded_again(page, tmp_path, service):
    """The fill pass after the upload must not upload again: on iCIMS that reloads the profile and clears passwords."""
    await icims_verify(page, tmp_path, "Resume202610211221.pdf")
    resume = tmp_path / "Alex_Doe_Resume.pdf"
    form = FormSession(page, Resolver(Profile(), service.db, service.config), resume, lambda *a: None)
    await form.scan()
    form.uploaded = True
    await form.fill_current(accept_prefilled=True)
    assert await page.evaluate("document.querySelector('input[type=file]').files.length") == 0


def test_the_icims_rename_is_read_where_innertext_joins_it_to_its_button():
    """Iridium's live page read "Resume202610211051.pdfReplace Resume": the name runs into the button text."""
    from types import SimpleNamespace

    form = SimpleNamespace(uploaded=True, page=SimpleNamespace(url="https://careers-iridium.icims.com/jobs/5186/x/candidate"))
    text = "Or please select your resume from one of the following:\nResume202610211051.pdfReplace Resume\n"
    assert FormSession.icims_renamed_resume(form, text)
    assert not FormSession.icims_renamed_resume(SimpleNamespace(uploaded=False, page=form.page), text)


async def test_a_capitalised_listed_resume_is_attached(page, tmp_path):
    # Pinpoint (Kenway 2026-10-27) prints the uploaded name in capitals and keeps only a hidden input.
    body = ("<h3>Résumé / CV</h3><div style='text-transform:uppercase'>Alex_Doe_Resume.pdf</div>"
            "<input type=hidden name='application_form[application][cv]' value='token'>")
    report = await verify_page(page, tmp_path, body)
    assert report["resume_attached"] is True


async def test_the_icims_frame_whose_src_keeps_the_email_steps_captcha_token_is_read(page, tmp_path):
    """Noblis 2026-10-26..27: the content iframe's src kept the email step's "h-captcha-response=..." query, so the frame
    was taken for a CAPTCHA and left out of the page evidence: the renamed resume never verified and was uploaded again
    on every pass. A real CAPTCHA frame (provider host and path) stays out."""
    resume = tmp_path / "Alex_Doe_Resume.pdf"
    resume.write_bytes(b"%PDF-1.4")
    outer = "https://careers-acme.icims.com/jobs/5186/engineer/candidate?mode=apply"
    inner = "https://careers-acme.icims.com/jobs/login?step=email&accept_gdpr=1&h-captcha-response=P1_synthetic&in_iframe=1"
    widget = "https://newassets.hcaptcha.com/captcha/v1/x/static/hcaptcha.html#frame=checkbox"
    pages = {
        outer: (f"<html><body><h1>Careers</h1><iframe id='icims_content_iframe' title='iCIMS Content iFrame' "
                f"src='{inner}' style='width:100%;height:400px'></iframe>"
                f"<iframe title='hCaptcha' src='{widget}' style='width:300px;height:80px'></iframe></body></html>"),
        inner: (f"<html><body><label>Name<input value='Alex Doe'></label>"
                f"{ICIMS_PAGE.format(name='Resume202610260542.pdf')}</body></html>"),
        widget.split("#")[0]: "<html><body><button>I am human</button></body></html>",
    }

    async def serve(route):
        await route.fulfill(body=pages[route.request.url.split("#")[0]], content_type="text/html")

    for url in pages:
        await page.route(url, serve)
    await page.goto(outer)
    await page.wait_for_load_state("load")
    form = FormSession(page, Resolver(Profile(), None, None), resume, lambda *a: None)
    urls = [frame.url for frame in await form.application_frames()]
    assert inner in urls and not any("hcaptcha.com" in url for url in urls)
    await form.scan()
    form.uploaded = True
    assert (await form.verify())["resume_attached"] is True
