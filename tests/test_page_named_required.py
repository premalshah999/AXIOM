"""Adzuna, 2026-10-28: the page refused the submit for a select, a text answer and a certification box, naming each in
single quotes; the read-back said ok (15 fields verified) because it only knew double-quoted names of at most 60
characters and never saw the box as a question. All pages here are synthetic."""

from jobpilot.answers import Resolver
from jobpilot.forms import NAMED_BY_PAGE, FormSession
from jobpilot.schemas import Profile

QUESTION = ("Do you certify that all the details mentioned in your resume and job application are accurate and "
            "truthful? Any false or misleading information may result in disqualification or termination of employment.")
PAGE = f"""<form>
<div style="color:red">Please enter a value for 'Are you currently based in the USA?'</div>
<label for=usa>Are you currently based in the USA?</label>
<select id=usa name=usa><option value="">Select an option</option><option>Yes</option><option>No</option></select>
<div style="color:red">Please choose at least one option for '{QUESTION}'</div>
<div class=q><div>{QUESTION}</div>
<label><input type=checkbox name=certify value=yes> Yes, I certify</label>
<label><input type=checkbox name=certify value=no> No, I don't certify</label></div>
</form>"""


def session(page, service, tmp_path, **policy):
    return FormSession(page, Resolver(Profile(**policy), service.db, service.config), tmp_path / "r.pdf", lambda *a: None)


def test_the_page_names_a_question_in_single_quotes_or_double_and_long_or_short():
    found = lambda text: NAMED_BY_PAGE.findall(text)
    assert found("Please enter a value for 'Are you currently based in the USA?'") == ["Are you currently based in the USA?"]
    assert found(f"Please choose at least one option for '{QUESTION}'") == [QUESTION]
    assert found('You must select a value for "State"') == ["State"]
    assert found("Please enter a value for ‘Phone’") == ["Phone"]
    # Apostrophes inside words and sentences that name nothing are not names.
    assert found("Please check that you're not a robot, it's quick") == []
    assert found("The applicant's resume was not accepted") == []


async def test_a_question_the_page_refuses_to_leave_blank_is_not_read_back_as_verified(page, service, tmp_path):
    await page.set_content(PAGE)
    form = session(page, service, tmp_path)
    report = await form.verify()
    assert not report["ok"]
    assert any("Are you currently based in the USA?" in p for p in report["problems"])
    assert any(p.startswith("Required field incomplete: Do you certify") for p in report["problems"])


async def test_the_certification_is_ticked_only_under_the_owners_permission_and_never_its_refusal(page, service, tmp_path):
    ticked = "[...document.querySelectorAll('input[type=checkbox]')].map(c => [c.value, c.checked])"
    await page.set_content(PAGE)
    allowed = session(page, service, tmp_path, allow_application_consents=True)
    await allowed.scan()
    await allowed.fill_current()
    assert await page.evaluate(ticked) == [["yes", True], ["no", False]]

    await page.set_content(PAGE)
    refused = session(page, service, tmp_path)  # "Accept privacy notices and certify accuracy" is off
    await refused.scan()
    await refused.fill_current()
    assert await page.evaluate(ticked) == [["yes", False], ["no", False]]
    assert any(q["question"].startswith("Do you certify") for q in refused.pending)


async def test_a_certification_that_also_waives_something_is_not_covered(page, service, tmp_path):
    waiver = QUESTION.replace("accurate and truthful?", "accurate and truthful, and you waive your right to arbitration?")
    await page.set_content(PAGE.replace(QUESTION, waiver))
    form = session(page, service, tmp_path, allow_application_consents=True)
    await form.scan()
    await form.fill_current()
    assert await page.evaluate("[...document.querySelectorAll('input[type=checkbox]')].some(c => c.checked)") is False
