"""Answers real forms stopped on, read from the saved pages of 2026-11-01. A required agreement whose choices are plainly accept and refuse
(Voya's arbitration agreement, Transamerica's "Have you read and understood these statements?"), a form's own sum (Arize's "What is 10 times
10?"), and a menu's "NA" for a question that only applies after a Yes (ALTEN's "If yes, please enter visa type")."""

import pytest

from jobpilot.answers import Resolver
from jobpilot.answers.consent import agreement_choice
from jobpilot.answers.local_rules import arithmetic_answer
from jobpilot.schemas import Profile

ARBITRATION = (
    "MUTUAL AGREEMENT TO ARBITRATE CLAIMS I understand that ExampleSoft is requiring me, as a condition of my application for employment, to "
    "arbitrate claims. The Company and I mutually consent to arbitration of all claims against its benefit plans or the plans' sponsors, "
    "fiduciaries and agents. The agreement remains applicable even if I am not employed by the Company."
)
AGREE = "I confirm that I have read, understand and agree to the written terms of the Mutual Agreement to Arbitrate Claims."
REFUSE = "I do not agree to the written terms of the Mutual Agreement to Arbitrate Claims."
STATEMENTS = (
    "No ExampleSoft company engages in the practice of polygraph testing for pre-employment screening. Applicants in some states, please note: "
    "I understand that it is unlawful to require a lie detector test as a condition of employment. Have you read and understood these statements?"
)


def field(label, options, kind="dropdown", **extra):
    return {"id": "f1", "label": label, "type": kind, "required": True, "options": options, **extra}


def resolver(service, terms=True, facts=None):
    profile = Profile.model_validate({"name": "Alex Example", "email": "alex@example.test", "autonomous": True,
                                      "accept_all_application_terms": terms, "facts": facts or {}})
    return Resolver(profile, service.db, service.config, job={"ats": "workday", "url": "https://example.test/job/1"})


def test_an_agreement_is_accepted_only_when_its_two_choices_say_accept_and_refuse():
    assert agreement_choice(ARBITRATION, ["Select One", AGREE, REFUSE]) == AGREE
    assert agreement_choice(STATEMENTS, ["Select One", "Yes", "No"]) == "Yes"
    # A plain Yes/No that is not about having read something is a question about the applicant.
    assert agreement_choice("Will you require sponsorship?", ["Yes", "No"]) is None
    assert agreement_choice("Do you have a criminal record? I understand this will be checked.", ["Yes", "No"]) is None
    # An option that itself states a fact is never this rule's to accept.
    assert agreement_choice("Work status", ["I confirm that I am authorized to work and agree", "I do not agree"]) is None
    assert agreement_choice("Terms", ["I agree", "Maybe later", "I do not agree"]) is None


@pytest.mark.parametrize("label,options,want", [(ARBITRATION, ["Select One", AGREE, REFUSE], AGREE), (STATEMENTS, ["Select One", "Yes", "No"], "Yes")])
def test_the_resolver_answers_them_when_the_applicant_accepts_terms(service, label, options, want):
    answer = resolver(service).local(field(label, options))
    assert answer and answer.disposition == "answer" and answer.value == want
    assert answer.evidence_ids == ["policy:consent"]


@pytest.mark.parametrize("label,options", [(ARBITRATION, ["Select One", AGREE, REFUSE]), (STATEMENTS, ["Select One", "Yes", "No"])])
def test_without_that_permission_they_stay_with_the_applicant(service, label, options):
    assert resolver(service, terms=False).local(field(label, options)) is None


@pytest.mark.parametrize("label,want", [
    ("What is 10 times 10?", "100"), ("What is 7 + 5?", "12"), ("3 x 4 =", "12"), ("What is 12 divided by 3?", "4"), ("How much is 9 minus 2", "7"),
    ("What is the sum of 3 and 4?", "7"), ("Please solve: 6 * 7", "42"), ("What is 10 times 10? *", "100"),
])
def test_a_sum_is_computed(service, label, want):
    assert arithmetic_answer(label) == want
    answer = resolver(service).local(field(label, [], "text"))
    assert answer and answer.value == want


@pytest.mark.parametrize("label", ["What is 10 years of experience worth?", "3 and 4", "Years of experience: 3 - 5", "What is your phone?", "2024 - 2026 employment", "7 / 0", "2024 - 2026"])
def test_what_is_not_a_sum_is_left_alone(label):
    assert arithmetic_answer(label) is None


def test_a_menus_na_answers_an_if_yes_after_a_no(service):
    r = resolver(service, facts={"requires_future_sponsorship_US": False, "work_authorization_US": "Yes", "us_citizen": False, "permanent_resident": False})
    answer = r.local(field("If yes, please enter visa type", ["H1B", "TN", "F1", "E2", "L1", "L2", "OTHER", "NA"], "combobox",
                           related_question={"label": "Will you now or in the future require sponsorship?", "value": "No", "options": ["Yes", "No"]}))
    assert answer and answer.value == "NA"
