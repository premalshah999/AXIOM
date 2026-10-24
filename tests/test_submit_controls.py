"""Final submit uniqueness: several Submit matches reduce to one only when it is unambiguous."""

from jobpilot.answers import Resolver
from jobpilot.forms import FormSession
from jobpilot.schemas import Profile


def _session(page, tmp_path):
    resume = tmp_path / "resume.pdf"
    resume.write_bytes(b"%PDF-1.4 test")
    return FormSession(page, Resolver(Profile(), None, None), resume, lambda *a: None)


async def test_hidden_submit_is_dropped_leaving_one_candidate(page, tmp_path):
    await page.set_content("""<form id=a><input name=email value=x>
      <button type=submit id=shown>Submit</button></form>
      <form id=b><button type=submit id=other>Submit</button></form>""")
    form = _session(page, tmp_path)
    await form.scan()
    assert len(await form.application_submit_controls()) == 2
    # Hidden after the scan: the live check must drop it, not rely on the scanner.
    await page.evaluate("document.getElementById('other').style.display='none'")
    final = await form.application_submit_controls()
    assert len(final) == 1
    assert await form.locator(final[0]["id"]).evaluate("b=>b.id") == "shown"


async def test_application_submit_wins_over_talent_community_sidebar(page, tmp_path):
    await page.set_content("""<main><form id=app><label>Name <input name=name></label>
      <label>Resume <input type=file name=resume></label>
      <button type=submit id=app-submit>Submit</button></form></main>
      <aside><form id=talent><h3>Join our Talent Community</h3>
      <input name=contact_email><input name=contact_phone>
      <button type=submit id=talent-submit>Submit</button></form></aside>""")
    form = _session(page, tmp_path)
    await form.scan()
    final = await form.application_submit_controls()
    assert len(final) == 1
    assert await form.locator(final[0]["id"]).evaluate("b=>b.id") == "app-submit"


async def test_talent_community_sidebar_beside_apply_is_a_signup(page, tmp_path):
    await page.set_content("""<header><button type=button>Apply</button></header>
      <aside><form><h3>Join our Talent Community</h3><input name=contact_email><input name=contact_phone>
      <button type=submit id=talent-submit>Submit</button></form></aside>""")
    form = _session(page, tmp_path)
    await form.scan()
    final = await form.application_submit_controls()
    assert await form.signup_form(final[0]["id"]) is True


async def test_application_with_attached_resume_and_community_question_is_not_a_signup(page, tmp_path):
    # Braze (Greenhouse, 2026-10-23): the attached resume replaces the file input, a header Apply button stays, and
    # the application itself asks about the talent community.
    await page.set_content("""<header><button type=button>Apply</button></header>
      <form><label>First Name <input name=first></label><label>Email <input name=email></label>
      <div>Resume/CV <span>resume.pdf</span> <button type=button>Remove file</button></div>
      <label>Select 'Yes' to join Braze's Talent Community and receive newsletters
      <select name=community><option>Yes</option><option>No</option></select></label>
      <button type=submit id=app-submit>Submit application</button></form>""")
    form = _session(page, tmp_path)
    await form.scan()
    final = await form.application_submit_controls()
    assert await form.signup_form(final[0]["id"]) is False


async def test_two_visible_submits_in_different_forms_stay_ambiguous(page, tmp_path):
    await page.set_content("""<form id=one><input name=first><button type=submit>Submit</button></form>
      <form id=two><input name=second><button type=submit>Submit</button></form>""")
    form = _session(page, tmp_path)
    await form.scan()
    final = await form.application_submit_controls()
    assert len(final) == 2


async def test_bare_apply_now_with_exclamation_is_the_resume_forms_submit(page, tmp_path):
    # Toast (Greenhouse): a header "Apply now" link plus the real "Apply now!" submit in the resume's form.
    await page.set_content("""<header><button type=button id=head>Apply now</button></header>
      <form id=app><label>First name <input name=first></label>
      <label>Resume <input type=file name=resume></label>
      <button type=submit id=real>Apply now!</button></form>""")
    form = _session(page, tmp_path)
    await form.scan()
    form.uploaded = form.upload_verified = True
    final = await form.application_submit_controls()
    assert [await form.locator(b["id"]).evaluate("b=>b.id") for b in final] == ["real"]


async def test_bare_apply_without_a_verified_resume_upload_is_not_a_submit(page, tmp_path):
    await page.set_content("""<form id=app><label>Resume <input type=file name=resume></label>
      <button type=submit id=real>Apply now!</button></form>""")
    form = _session(page, tmp_path)
    await form.scan()
    assert await form.application_submit_controls() == []


async def test_successfactors_review_step_apply_is_the_submit(page, tmp_path):
    # ASSA ABLOY 2026-10-15: "7. Review" shows the answers as text and keeps only the resume inputs.
    # Its Apply was not recognized as the submit, so a preview run clicked it and applied.
    review = """<h2>7. Review</h2><p>Phone: (555) 555-0100</p>
      <label>Resume/CV <input type=file name=resume></label>
      <button type=button id=apply>Apply</button><button type=button id=close>Close</button>"""
    url = "https://career2.successfactors.eu/portalcareer?company=assaabloya&lang=en_US"
    await page.route(url, lambda route: route.fulfill(status=200, content_type="text/html", body=review))
    await page.goto(url)
    form = _session(page, tmp_path)
    form.resolver.job = {"ats": "successfactors"}
    await form.scan()
    final = await form.application_submit_controls()
    assert [await form.locator(b["id"]).evaluate("b=>b.id") for b in final] == ["apply"]


async def test_successfactors_posting_apply_now_is_not_the_submit(page, tmp_path):
    posting = """<label>Resume <input type=file name=resume></label><a href="#" id=entry>Apply now »</a>"""
    url = "https://assaabloy.jobs2web.com/job/Austin-AI-Scientist-TX-78753/1441539933/"
    await page.route(url, lambda route: route.fulfill(status=200, content_type="text/html", body=posting))
    await page.goto(url)
    form = _session(page, tmp_path)
    form.resolver.job = {"ats": "successfactors"}
    await form.scan()
    assert await form.application_submit_controls() == []


async def test_successfactors_confirmation_text_is_proof(page, tmp_path):
    # EY 2026-10-16: the classic career page says so once the form is gone; the run stayed "waiting".
    done = """<header><input type=search placeholder="Search jobs"></header>
      <p>You have successfully submitted your application to this role.</p><a href="#">Job search</a>"""
    url = "https://career5.successfactors.eu/career?company=EYHRISPRD1&career_ns=job_application"
    await page.route(url, lambda route: route.fulfill(status=200, content_type="text/html", body=done))
    await page.goto(url)
    form = _session(page, tmp_path)
    form.resolver.job = {"ats": "successfactors", "url": "https://careers.ey.com/ey/job/x/1/"}
    receipt = await form.proof()
    assert receipt and receipt["evidence_selector"] == "successfactors_confirmation_text"


async def test_successfactors_form_with_apply_is_not_proof(page, tmp_path):
    form_page = """<p>Thank you for applying to our company. Please complete the form.</p>
      <label>Phone <input name=phone></label><button>Apply</button>"""
    url = "https://career5.successfactors.eu/career?company=EYHRISPRD1&career_ns=job_application"
    await page.route(url, lambda route: route.fulfill(status=200, content_type="text/html", body=form_page))
    await page.goto(url)
    form = _session(page, tmp_path)
    form.resolver.job = {"ats": "successfactors", "url": "https://careers.ey.com/ey/job/x/1/"}
    assert not await form.proof()


async def test_successfactors_resume_listed_in_my_documents_counts_as_attached(page, tmp_path):
    # Corning 2026-10-16: the profile's My Documents lists the resume; the form has no upload of its own,
    # so the run never counted a resume and kept filling a verified form.
    portal = """<h3>My Documents</h3><a href="#">resume.pdf (10/08/2026)</a>
      <label>Have you ever worked for Corning? <select><option>No</option></select></label>
      <button type=button id=apply>Apply</button>"""
    url = "https://career4.successfactors.com/portalcareer?company=CNGPROD&lang=en_US"
    await page.route(url, lambda route: route.fulfill(status=200, content_type="text/html", body=portal))
    await page.goto(url)
    form = _session(page, tmp_path)
    form.resolver.job = {"ats": "successfactors"}
    await form.scan()
    report = await form.verify()
    assert report["resume_attached"]
    final = await form.application_submit_controls()
    assert [await form.locator(b["id"]).evaluate("b=>b.id") for b in final] == ["apply"]


async def test_header_submit_button_outside_the_form_is_not_the_final_submit(page, tmp_path):
    # Sparksoft (Greenhouse job board, 2026-10-18): the header button "Submit Application" only scrolls to the form.
    await page.set_content("""<div class=job__header><h1>Software Engineer</h1>
      <button type=button class="btn btn--pill" id=header>Submit Application</button></div>
      <form id=application-form><label>First Name <input name=first_name></label>
      <div class=application--submit><button type=submit class="btn btn--pill" id=real>Submit application</button></div></form>""")
    form = _session(page, tmp_path)
    await form.scan()
    final = await form.application_submit_controls()
    assert len(final) == 1
    assert await form.locator(final[0]["id"]).evaluate("b=>b.id") == "real"


async def test_two_native_submits_in_different_forms_stay_ambiguous(page, tmp_path):
    await page.set_content("""<form id=a><input name=x><button type=submit id=one>Submit</button></form>
      <form id=b><input name=y><button type=submit id=two>Submit</button></form>""")
    form = _session(page, tmp_path)
    await form.scan()
    assert len(await form.application_submit_controls()) == 2


async def test_an_icims_packet_bar_repeated_above_and_below_is_one_submit(page, tmp_path):
    """SAS 2026-10-21: "Save & Return Later | Submit" above and below the packet form, both name=icims_submit, outside
    one shared form; the final Submit was refused as "not unique" three times."""
    await page.set_content("""<div><input type=submit name=icims_save value="Save &amp; Return Later">
      <input type=submit name=icims_submit value=Submit id=top></div>
      <form id=packet><label>Name <input name=name></label></form>
      <div><input type=submit name=icims_save value="Save &amp; Return Later">
      <input type=submit name=icims_submit value=Submit id=bottom></div>""")
    form = _session(page, tmp_path)
    await form.scan()
    final = await form.application_submit_controls()
    assert len(final) == 1
    assert await form.locator(final[0]["id"]).evaluate("b=>b.id") == "bottom"
