"""A role closed to the applicant's status is not submitted (2026-10)."""

from types import SimpleNamespace

import pytest

from jobpilot.browser import BrowserEngine
from jobpilot.eligibility import check_eligibility, closed_by_statement
from jobpilot.schemas import Profile

OPT = {
    "us_citizen": False,
    "country_of_citizenship": "Freedonia",
    "work_authorization_US": "Yes, authorized to work for any employer in the USA (applicant confirmed)",
    "requires_future_sponsorship_US": False,
    "current_immigration_status_US": "OPT; expiration July 2029. Provide only when specifically asked for status or expiration.",
}
CITIZEN = {"us_citizen": True, "work_authorization_US": True, "requires_future_sponsorship_US": False}
GREEN_CARD = {"us_citizen": False, "permanent_resident": True, "work_authorization_US": True}
# Atlassian 9ca72eb8: answered "I cannot make that confirmation" and submitted anyway.
NOT_OPEN = (
    "Please review the following statement and confirm your understanding: This role is not open to candidates whose work "
    "authorization is based on F-1 student status or J-1 status. Example Co does not complete or sign employer verification "
    "or training-plan forms concerning work authorization status for this role, including forms such as Form I-983 for STEM "
    "OPT. If your legal right to work for this role would require Example Co to take any such action, you are not eligible "
    "for this position."
)
NON_CITIZENS = "Are you a citizen of the United States? (Non-Citizens will not be eligible to move forward in the hiring process.)"


def profile(facts):
    return Profile(facts=facts)


@pytest.mark.parametrize(
    "text",
    [
        NOT_OPEN,
        "This position is not open to F-1 OPT candidates.",
        "We do not accept candidates on OPT or CPT.",
        "J-1 and F-1 visa holders are not eligible for this role.",
        NON_CITIZENS,
        "This role is not open to non-citizens.",
        "This position is open only to U.S. citizens.",
        "An applicable law requires us to limit this position to individuals who are U.S. citizens. Are you a U.S. citizen?",
        "This role requires you to be a U.S. citizen. Are you a U.S. citizen?",
    ],
)
def test_a_statement_closes_the_role_to_this_applicant(text):
    assert closed_by_statement(text, profile(OPT))
    assert closed_by_statement(text, profile(CITIZEN)) is None


def test_a_green_card_holder_is_not_closed_out_by_a_visa_statement():
    assert closed_by_statement(NOT_OPEN, profile(GREEN_CARD)) is None
    assert closed_by_statement("This position is open only to U.S. citizens or permanent residents.", profile(GREEN_CARD)) is None
    assert closed_by_statement("This position is open only to U.S. citizens or permanent residents.", profile(OPT))


@pytest.mark.parametrize(
    "text",
    [
        "Many of our positions require U.S. citizenship. Please indicate your citizenship status.",
        "I understand that certain jobs may require that I am a United States Citizen.",
        "Are you able to provide proof of a permanent right to work without sponsorship (H1-B, OPT, EAD do not qualify)?",
        "OPT candidates are not eligible unless they hold a degree in a STEM field.",
        "Only US Citizens or aliens who have a legal right to work in the U.S. are eligible for employment.",
        "Do you have the ability to obtain a U.S. Security Clearance for which the U.S. Government requires U.S. Citizenship?",
        "Candidates on H-1B are not eligible for this role.",
        "Are you a U.S. citizen?",
    ],
)
def test_other_wording_does_not_close_it(text):
    assert closed_by_statement(text, profile(OPT)) is None


def test_the_posting_closes_it_before_the_form():
    conflicts = check_eligibility("This role is not open to candidates on F-1 student status.", profile(OPT)).conflicts
    assert [f.subject for f in conflicts] == ["visa_status"]
    assert not check_eligibility("This role is not open to candidates on F-1 student status.", profile(CITIZEN)).conflicts
    assert [f.subject for f in check_eligibility("This role is not open to non-citizens.", profile(OPT)).conflicts] == ["citizenship"]


def engine(facts, fields=None, ledger=None):
    e = object.__new__(BrowserEngine)
    e.form = SimpleNamespace(
        resolver=SimpleNamespace(profile=profile(facts)), fields=fields or {}, ledger=ledger or {}, failed_strategies={}
    )
    e.emit = lambda *a, **k: None
    return e


def test_the_form_closes_it_at_finish_even_from_an_earlier_page():
    result = engine(OPT, ledger={"old": {"label": NOT_OPEN}}).status_gate_result()
    assert result["state"] == "closed" and result["blocker_type"] == "ineligible" and "F-1 student status" in result["reason"]
    assert engine(OPT, {"f": {"label": NOT_OPEN, "value": "I cannot make that confirmation"}}).status_gate_result()["state"] == "closed"
    assert engine(CITIZEN, ledger={"old": {"label": NOT_OPEN}}).status_gate_result() is None
    assert engine(OPT, {"f": {"label": "Are you a U.S. citizen?"}}).status_gate_result() is None


def test_more_wordings_of_required_citizenship_close_on_the_answer_no():
    e = engine(OPT, {"f": {"label": "This role requires you to be a U.S. citizen. Are you a U.S. citizen?", "value": "No"}})
    assert e.citizenship_required_result()["state"] == "closed"
    e = engine(OPT, {"f": {"label": "This position requires current U. S. citizenship. Are you currently a U. S. citizen?", "value": "No"}})
    assert e.citizenship_required_result()["state"] == "closed"
