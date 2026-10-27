"""SuccessFactors' classic career site replaces the form with its success screen after applyJob succeeds (L3Harris
2026-10-27: an empty message area and "Back to Job Listings" / "View Profile"); six submissions read as unconfirmed."""

from jobpilot.answers import Resolver
from jobpilot.forms import FormSession
from jobpilot.schemas import Profile

SUCCESS = """<main><div class="rcmSuccessMsg"></div>
<div class="rcmSuccessBackToResultsBtn" role="button">Back to Job Listings</div>
<div class="rcmSuccessBackToResultsBtn" role="button">View Profile</div></main>"""
FORM = SUCCESS + "<label>Phone<input id=phone></label>"


async def proof(page, tmp_path, html):
    await page.set_content(html)
    form = FormSession(page, Resolver(Profile(), None, None, job={"ats": "successfactors", "id": "j"}),
                       tmp_path / "r.pdf", lambda *a, **k: None)
    return await form.proof()


async def test_the_success_screen_is_the_receipt(page, tmp_path):
    receipt = await proof(page, tmp_path, SUCCESS)
    assert receipt and receipt["evidence_selector"] == "div.rcmSuccessBackToResultsBtn"


async def test_a_page_with_a_form_left_is_not(page, tmp_path):
    receipt = await proof(page, tmp_path, FORM)
    assert not receipt or receipt["evidence_selector"] != "div.rcmSuccessBackToResultsBtn"
