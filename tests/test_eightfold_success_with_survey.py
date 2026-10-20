"""Eightfold (Qualcomm 2026-10-17): "Thank you for your application, Alex!" in #form-submission-success-message sat
above a star-rating survey; the survey's inputs made the generic proof treat the page as an open form."""

from jobpilot.answers import Resolver
from jobpilot.forms import FormSession
from jobpilot.schemas import Profile

PAGE = """<header><a href="#">Search for Jobs</a></header>
<div class="success"><h4 id="form-submission-success-message">Thank you for your application, Alex!</h4>
<div class="survey"><p>Rate your application experience.</p>
<label><input type="radio" name="r" value="1">1</label><label><input type="radio" name="r" value="2">2</label></div>
<button>Browse Jobs</button><button>Track your application</button></div>"""


async def form_for(page, tmp_path, html):
    resume = tmp_path / "r.pdf"
    resume.write_bytes(b"%PDF")
    await page.set_content(html)
    resolver = Resolver(Profile(), None, None, job={"ats": "eightfold", "url": "https://careers.example.test/careers/job/1"})
    return FormSession(page, resolver, resume, lambda *a, **k: None)


async def test_the_success_message_above_a_survey_is_proof(page, tmp_path):
    receipt = await (await form_for(page, tmp_path, PAGE)).proof()
    assert receipt and receipt["evidence_selector"] == "#form-submission-success-message"


async def test_a_form_that_still_offers_submit_is_not(page, tmp_path):
    html = PAGE.replace("<button>Browse Jobs</button>", "<button>Submit application</button>")
    receipt = await (await form_for(page, tmp_path, html)).proof()
    assert not receipt or receipt.get("evidence_selector") != "#form-submission-success-message"


async def test_qualcomms_empty_state_title_is_proof(page, tmp_path):
    html = """<div class="empty-module_empty-wrapper__a1"><div class="empty-module_empty-title__YRmfs">Thank you for your
      application, Alex!</div><div class="survey"><p>Rate your application experience.</p></div>
      <button><span>Browse Jobs</span></button><button><span>Track your application</span></button></div>"""
    receipt = await (await form_for(page, tmp_path, html)).proof()
    assert receipt and "Thank you for your application" in receipt["confirmation"]



async def test_a_thank_you_modal_over_the_profile_is_proof(page, tmp_path):
    # Boston Scientific 2026-10-17: "Thank you for your application, Alex!" in a modal with a Continue button.
    html = """<header>Dashboard Profile</header><div role="dialog" aria-modal="true"><button aria-label="Close">x</button>
      <p>Thank you for your application, Alex!</p><button>Continue</button></div>"""
    receipt = await (await form_for(page, tmp_path, html)).proof()
    assert receipt and "Thank you for your application" in receipt["confirmation"]
