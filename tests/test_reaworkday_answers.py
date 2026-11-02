"""Workday questions that stopped runs on 2026-11-01, read from their saved pages: a generation menu named by birth years (Rocket) and a
procedure acknowledged only with "I Acknowledge" (Pennant's I-9 agreement). Synthetic applicant."""

import pytest

from jobpilot.answers import Resolver
from jobpilot.answers.consent import agreement_choice
from jobpilot.answers.local_rules import generation_choice
from jobpilot.schemas import Profile

GENERATIONS = ["Select One", "1946 - 1964 (Baby Boomers)", "1965 - 1980 (Generation X)", "1981 - 1996 (Generation Y/Millennial)",
               "1997 - 2009 (Generation Z)", "2010 - Onward", "Prefer not to answer"]
I9 = ("I agree to submit the Employment Verification Form (I-9) within three business days of employment, providing documentation for "
      "identity and eligibility. A valid social security number is required for reporting wages to state and federal governments.")


def field(label, options, kind="dropdown"):
    return {"id": "f1", "label": label, "type": kind, "required": True, "options": options}


def resolver(service, terms=True, facts=None):
    profile = Profile.model_validate({"name": "Alex Example", "email": "alex@example.test", "autonomous": True,
                                      "accept_all_application_terms": terms, "facts": facts or {}})
    return Resolver(profile, service.db, service.config, job={"ats": "workday", "url": "https://example.test/job/1"})


@pytest.mark.parametrize("year,want", [(2003, "1997 - 2009 (Generation Z)"), (1990, "1981 - 1996 (Generation Y/Millennial)"),
                                       (1970, "1965 - 1980 (Generation X)"), (2012, "2010 - Onward"), (1930, None), (None, None)])
def test_generation_is_the_menu_range_holding_the_birth_year(year, want):
    assert generation_choice("Which generation applies to you?", GENERATIONS, year) == want


def test_a_menu_that_is_not_about_generation_is_left_alone():
    assert generation_choice("Which period applies?", GENERATIONS, 2003) is None


def test_the_resolver_answers_generation_from_the_recorded_birth_date(service):
    answer = resolver(service, facts={"date_of_birth": "03/04/2003"}).local(field("Which generation applies to you?", GENERATIONS))
    assert answer and answer.value == "1997 - 2009 (Generation Z)" and answer.evidence_ids == ["fact:date_of_birth"]
    assert resolver(service).local(field("Which generation applies to you?", GENERATIONS)) is None


def test_a_bare_acknowledgement_of_a_procedure_is_accepted():
    assert agreement_choice(I9, ["Select One", "I Acknowledge", "I Do Not Acknowledge"]) == "I Acknowledge"


@pytest.mark.parametrize("label", [
    "I certify that I have never been convicted of a felony.", "I swear under penalty of perjury that I have registered with the Selective Service.",
    "I agree that I am authorized to work in the United States.",
])
def test_an_oath_or_a_statement_about_the_applicant_is_never_accepted_that_way(label):
    assert agreement_choice(label, ["I Acknowledge", "I Do Not Acknowledge"]) is None


def test_the_resolver_needs_the_accept_terms_permission(service):
    options = ["Select One", "I Acknowledge", "I Do Not Acknowledge"]
    answer = resolver(service).local(field(I9, options))
    assert answer and answer.value == "I Acknowledge"
    assert resolver(service, terms=False).local(field(I9, options)) is None


ACK = ["Select One", "I Acknowledge", "I Do Not Acknowledge"]


@pytest.mark.parametrize("label", [
    "I acknowledge that I am at least 18 years of age.",
    "I acknowledge that I hold a valid driver's license.",
    "I acknowledge that I have a valid driver's license and a clean driving record.",
    "I agree to provide proof that I am at least 18 years of age.",
    "You must be at least 18 years of age to apply. I acknowledge.",
    "I acknowledge that I have been employed here before.",
    "I agree that I meet the minimum qualifications.",
])
def test_a_factual_declaration_is_never_a_procedure_to_acknowledge(label):
    assert agreement_choice(label, ACK) is None


@pytest.mark.parametrize("label", [
    "I acknowledge that I have read the Candidate Privacy Notice.",
    "I agree to bring my identity documents to the first day of work.",
    "I accept the arbitration agreement described above.",
])
def test_reading_and_procedures_are_still_acknowledged(label):
    assert agreement_choice(label, ACK) == "I Acknowledge"


def test_the_resolver_leaves_an_age_claim_to_its_fact_check_even_for_a_minor(service):
    minor = {"date_of_birth": "03/04/2015", "age": "10"}
    claim = "I acknowledge that I am at least 18 years of age."
    assert resolver(service, facts=minor).local(field(claim, ACK)) is None
    assert resolver(service, facts=minor).local(field(I9, ACK)).value == "I Acknowledge"


def test_a_question_only_called_other_is_never_answered_from_what_another_other_was_told():
    """One person's reply to an "Other" follow-up was typed into an optional "Other" social-network URL: "Invalid URL", five stopped runs."""
    from jobpilot.answers.validate import BARE_OTHER

    assert BARE_OTHER.fullmatch("Other") and BARE_OTHER.fullmatch("Other *") and BARE_OTHER.fullmatch(" Others: ")
    assert not BARE_OTHER.fullmatch("Other names you have used") and not BARE_OTHER.fullmatch("If other, please specify")


def test_required_questions_no_saved_fact_backs_are_put_to_the_person_with_the_pages_own_options():
    from jobpilot.browser import unbacked_answers_result

    waiver = {"question": "Waiver I waive any privileges and I release from any liability all persons.", "options": ["I agree", "I don't agree"],
              "key": "answers-v9:waiver", "reason": "disclosure_scope", "blocker_type": "model_output", "retryable": True, "required": True}
    result = unbacked_answers_result([waiver])
    assert result["state"] == "needs_review" and result["blocker_type"] == "missing_fact"
    review = result["reviews"][0]
    assert review["options"] == ["I agree", "I don't agree"] and review["blocker_type"] == "missing_fact" and review["retryable"] is False
    assert "disclosure_scope" not in str(review)  # Engine words never reach the person.
    assert unbacked_answers_result([{**waiver, "key": ""}])["state"] == "waiting_browser"
