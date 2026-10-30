"""A commit needs evidence on the open page that it is this application's last step.

Two Sigma (Avature, 2026-10-30): signing in landed on a blank page with one Submit. verify() had nothing on the page to
read, and "5 verified fields" and the resume were the ledger of the sign-in and account pages, so the page passed as the
final review. The real form (15 questions, then Confirm Application) was still ahead. A real review page has no editable
field either, so it has to keep passing: production committed 21 of 1,591 applications from one (2026-10-22..30).
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from jobpilot.answers import Resolver
from jobpilot.browser import BrowserEngine
from jobpilot.forms import FormSession
from jobpilot.schemas import Profile

SUBMIT = {"id": "submit-1", "label": "Submit"}


def record(label, answer, section=""):
    return {"label": label, "answer": answer, "verified": True, "comparison": {"section": section}}


# The incident's own ledger: every "verified field" came from the account and sign-in pages.
ACCOUNT_LEDGER = {
    "a": record("First Name", "Alex", "Account Information"),
    "b": record("Last Name", "Example", "Account Information"),
    "c": record("Email Address", "alex@example.test", "Account Information"),
    "d": record("Phone Number", "3015550100", "Account Information"),
    "e": record("Email Address", "alex@example.test", "Login"),
}


def form(ledger=None):
    return SimpleNamespace(
        ledger=ledger or {},
        resume_path=SimpleNamespace(name="Alex_Example_Resume.pdf"),
        resolver=SimpleNamespace(job={"title": "Staff Example Engineer"}),
    )


def evidence(text="", fields=(), ledger=None, control=SUBMIT):
    return FormSession.final_step_evidence(form(ledger), {"fields": list(fields), "text": text}, control)


@pytest.mark.parametrize(
    "text,ledger",
    [
        ("", ACCOUNT_LEDGER),  # the incident
        ("Submit", ACCOUNT_LEDGER),  # the control's own label is no evidence
        # Only the site's furniture around a lone Submit.
        ("Careers Open Roles Events Privacy Policies Legal Disclosures Terms Application FAQs Submit", ACCOUNT_LEDGER),
        # A signed-in header repeats the account's name and email on every page of the site.
        ("Alex Example alex@example.test Careers Submit", ACCOUNT_LEDGER),
        # A short or boolean answer appears on any page by chance.
        ("Yes No None true Submit", {"a": record("Authorized?", "Yes", "Work"), "b": record("Veteran", "None", "Work"),
                                     "c": record("Agree", "true", "Work")}),
        # A secret never counts, whatever its section.
        ("hunter2hunter2 Submit", {"a": record("Password", "hunter2hunter2", "Work")}),
    ],
)
def test_a_page_with_nothing_of_the_application_has_no_evidence(text, ledger):
    assert evidence(text, ledger=ledger) == ""


def test_a_sign_in_form_is_not_an_applicant_field():
    assert evidence(fields=[{"type": "password", "label": "Password"}]) == ""
    assert evidence(fields=[{"type": "text", "label": "Verification code"}]) == ""
    assert evidence(fields=[{"type": "text", "label": "Portal user", "site_account": True}]) == ""


def test_the_control_label_does_not_vouch_for_itself():
    review = {"id": "submit-1", "label": "Review and Submit my application"}
    assert evidence("Review and Submit my application", control=review) == ""
    assert evidence("Please review. Review and Submit my application", control=review) == "wording"
    assert evidence("REVIEW AND SUBMIT MY APPLICATION", control=review) == ""  # CSS capitalized it on the page.


@pytest.mark.parametrize(
    "text,kind",
    [
        ("Review your answers before you send them. Submit", "wording"),
        ("Step 4 of 4 Submit", "wording"),
        ("Confirm Application Submit", "wording"),
        ("Please certify that the above is true. Submit", "wording"),
        ("I acknowledge the statement above. Submit", "wording"),
        ("You are about to send your application. Submit", "wording"),
        ("Resume on file: Alex_Example_Resume.pdf Submit", "resume"),
        ("You are applying for Staff Example Engineer in Springfield. Submit", "job"),
        ("Current city Springfield Years of experience 4 Submit", "answer"),
    ],
)
def test_a_review_page_without_a_field_shows_what_it_reviews(text, kind):
    ledger = {**ACCOUNT_LEDGER, "x": record("Current city", "Springfield", "About you")}
    assert evidence(text, ledger=ledger) == kind


def test_a_page_with_a_visible_applicant_field_needs_nothing_else():
    assert evidence(fields=[{"type": "text", "label": "Current city"}]) == "field"
    assert evidence(fields=[{"type": "checkbox", "label": "I agree"}]) == "field"


async def test_evidence_is_read_from_a_real_blank_page_with_hidden_fields(page, service, tmp_path):
    """The incident's page: hidden Application Date and source fields, one Submit, nothing to see."""
    await page.set_content("""
      <style>.WizardFieldHidden{display:none}</style>
      <form><div class="WizardFieldHidden"><label for="d">Application Date</label><input id="d" type="date"></div>
      <div class="WizardFieldHidden"><label for="s">Your application source</label><select id="s"><option>Other</select></div>
      <button type="submit" style="width:0;height:0;padding:0;border:0;overflow:hidden">Submit</button></form>""")
    form_session = FormSession(page, Resolver(Profile(), service.db, service.config), tmp_path / "r.pdf", lambda *a: None)
    form_session.ledger = dict(ACCOUNT_LEDGER)
    obs = await form_session.scan()
    assert obs["fields"] == [] and [b["label"] for b in obs["controls"]] == ["Submit"]
    assert form_session.final_step_evidence(obs, obs["controls"][0]) == ""


async def test_the_engine_refuses_the_commit_with_nothing_reserved(page, service, tmp_path):
    await page.set_content('<form><button type="submit">Submit</button></form>')
    form_session = FormSession(page, Resolver(Profile(), service.db, service.config), tmp_path / "r.pdf", lambda *a: None)
    form_session.ledger = dict(ACCOUNT_LEDGER)
    form_session.verify = AsyncMock(
        return_value={"ok": True, "resume_attached": True, "verified_fields": 5, "problems": [], "pending": []}
    )

    async def none(*a, **k):
        return None

    engine = object.__new__(BrowserEngine)
    engine.form, engine.page, engine.armed, engine.result = form_session, page, False, None
    engine.run_id = "r"
    engine.job = {"id": "j", "demo": False, "ats": "custom", "url": "https://careers.example.test/x"}
    engine.run_record = {"mode": "submit", "packet": {"resume_sha": "sha"}}
    engine.events = []
    engine.emit = lambda kind, message, data=None: engine.events.append(kind)
    engine.quality_gate = none
    engine.browser_challenge = none
    reservations = []
    engine.service = SimpleNamespace(reserve_submission=reservations.append)
    result = await engine.finish()
    assert result["state"] == "waiting_browser" and result["recoverable"] is True
    assert "not the final step" in result["reason"] and "'Submit'" in result["reason"]
    assert engine.armed is False and reservations == [] and "submitting" not in engine.events
    assert "finish_refused" in engine.events, "the refusal is explained in the run's events"
