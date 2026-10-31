"""Answers a misread or over-strict rule kept from reaching the page (Wave 9): synthetic profile throughout."""
import pytest

from jobpilot.answers import Resolver
from jobpilot.answers.validate import ValidateMixin
from jobpilot.features.knowledge import attest
from jobpilot.features.knowledge.attestations import EMPLOYMENT_HISTORY
from jobpilot.forms import implausible_prefill
from jobpilot.knowledge import relevant_fact
from jobpilot.schemas import Profile

JOB = {"company": "Acme", "url": "https://acme.wd5.myworkdayjobs.com/job/42"}


def field(label, options=(), kind="select", **extra):
    return {"id": "q", "label": label, "type": kind, "required": True, "options": list(options), "value": "", "valid": True, **extra}


def resolver(service, facts=None, work=None):
    return Resolver(
        Profile(facts=facts or {}, work=work or [{"company": "Example University", "title": "Analyst", "current": True}]),
        service.db, service.config, employer="Acme", job=JOB,
    )


def test_a_device_question_is_answered_with_the_computer(service):
    options = ["Computer/Laptop", "Mobile Phone", "iPad/Tablet"]
    a = resolver(service).local(field("Please indicate which type of device you are using to apply.", options, "combobox"))
    assert a and a.disposition == "answer" and a.value == "Computer/Laptop"
    # A pick list that offers no computer is no device question this rule can answer.
    assert resolver(service).local(field("Which type of device you are using to apply?", ["Phone", "Tablet"], "combobox")) is None


BENEFITS = "I or a household member receive or received the following government benefits:"


def none_box(items, caption=BENEFITS):
    return field("None of the Above", (), "checkbox", list_caption=caption, list_items=items)


def test_none_of_the_above_is_ticked_only_over_a_list_the_screener_denies(service):
    r = resolver(service, {"tax_credit_screening_no": True})
    ok = r.local(none_box(["Food Stamps (SNAP) / AFDC / TANF", "Not sure"]))
    assert ok and ok.value == "true" and ok.evidence_ids == ["fact:tax_credit_screening_no"]
    ok = r.local(none_box(["Served in the US Military", "Received Rehab", "Received SSI", "Been Unemployed",
                           "Been Convicted of a Felony", "A spouse who is a member of the Armed Forces of the United States."],
                          "I have:"))
    assert ok and ok.value == "true"
    # A list with a box the screener never asked about is not decided by it.
    other = r.local(none_box(["Served in the US Military", "A physical disability"], "I have:"))
    assert other is None or other.value != "true"
    # Nor is the screener's No an answer for an applicant who never gave it.
    plain = resolver(service).local(none_box(["Not sure", "Received SSI"]))
    assert plain is None or plain.value != "true"


QUESTION = (
    "Second job policy restriction: Our policy does not allow employees to maintain employment with a gaming establishment "
    "or its subcontractors. If you currently work in the gaming industry, would you be able to end that employment before "
    "beginning work with our Company?"
)
GAMING = ["Yes, I can end that employment before starting", "No, I cannot end that employment before starting",
          "I do not work for a gaming establishment"]


def test_a_current_industry_question_is_answered_from_the_attested_history(service):
    attest(service.db, "employment_history_complete", EMPLOYMENT_HISTORY["statement"])
    a = resolver(service).local(field(QUESTION, GAMING, "dropdown"))
    assert a and a.value == GAMING[2] and "fact:employment_history_complete" in a.evidence_ids and "profile:work.0" in a.evidence_ids
    # A current role in that industry is the applicant's to answer.
    casino = [{"company": "Example Gaming Group", "title": "Dealer", "current": True}]
    assert resolver(service, work=casino).local(field(QUESTION, GAMING, "dropdown")) is None


def test_the_industry_question_needs_the_attestation(service):
    assert resolver(service).local(field(QUESTION, GAMING, "dropdown")) is None


CJIS = (
    "Some roles require the ability to obtain and maintain Criminal Justice Information Services (CJIS) certification as a "
    "condition of employment. This includes meeting all FBI CJIS Security Policy requirements, including a fingerprint-based "
    "background check. Are you able to meet these requirements if certification is required?"
)


def test_the_conviction_fact_supports_an_ability_to_clear_a_records_check():
    assert relevant_fact(CJIS, "criminal_conviction")
    assert not relevant_fact(CJIS, "willing_to_relocate")
    # A drug screen, a credit check, a consent and a willingness are other subjects.
    assert not relevant_fact("Are you able to pass a pre-employment drug screen and background check?", "criminal_conviction")
    assert not relevant_fact("Do you consent to a background check?", "criminal_conviction")
    assert not relevant_fact("Are you willing to undergo a background check?", "criminal_conviction")


def test_a_pick_list_named_for_a_site_is_not_a_link_box():
    menu = {"type": "select", "label": "Company Website .", "value": "Company Website", "options": ["Career Section", "Company Website"]}
    assert not implausible_prefill(menu)
    assert implausible_prefill({"type": "text", "label": "Company Website", "value": "Company Website", "options": []})
