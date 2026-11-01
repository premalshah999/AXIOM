"""What decides a question's scoped subject. Words in passing made an answer "out of scope" and stopped real applications:
"reporting wages to state and federal governments" (an I-9 acknowledgement), "the communications referred to in this
application" (a waiver), "matters related to my suitability for employment" (a background notice), "eliminate corrupted
records" (a quiz option), a certification's "Sponsoring Organization" and a "Second job policy restriction"."""

import pytest

from jobpilot.answers.consent import critical_question
from jobpilot.knowledge import relevant_fact, topic


@pytest.mark.parametrize("label,subject", [
    ("I agree to submit the Employment Verification Form (I-9) within three business days of employment, providing "
     "documentation for identity and eligibility. A valid social security number is required for reporting wages to state "
     "and federal governments.", "consent"),
    ("Waiver I waive any privileges regarding the information and communications referred to in this application, and I "
     "release from any liability all persons and organizations involved in these communications.", ""),
    ("I understand I may be subject to a pre-employment background check to investigate my criminal background and other "
     "matters related to my suitability for employment. Initial to agree.", "background_check"),
    ("C. Rebuild the database immediately to eliminate corrupted records.", ""),
    ("Sponsoring Organization", ""),
    ("Second job policy restriction: Our policy does not allow employees to maintain employment with a gaming establishment "
     "or its subcontractors. If you currently work in the gaming industry, would you be able to end that employment before "
     "beginning work with our Company?", ""),
    ("Do you have or have you had any restrictions on your professional license / certification?", ""),
    ("a. If NO, please indicate your country of citizenship and, if different, the country in which you have most recently "
     "obtained citizenship or permanent residency status. This information will be used to determine whether it would be "
     "possible to obtain any required license from the U.S. government.", "citizenship"),
    ("What level of DOD clearance do you have?", "clearance"),
])
def test_words_in_passing_decide_nothing(label, subject):
    assert topic(label) == subject


@pytest.mark.parametrize("label,subject", [
    ("Have you ever been employed by a state government or the United States government?", "government"),
    ("Would you be subject to any restrictions from a former employer upon joining ExampleSoft?", "restriction"),
    ("Are you aware of any contractual clauses that would restrict you from working with us?", "restriction"),
    ("Are you subject to a non-compete agreement, or any other potential restriction to your employment?", "restriction"),
    ("Were you referred to ExampleSoft by a current employee?", "referral"),
    ("Are you related to any employee of ExampleSoft?", "referral"),
    ("Do you need ExampleSoft to sponsor you for a visa?", "sponsorship"),
    ("Have you ever been convicted of corruption or bribery?", "conviction"),
])
def test_the_real_questions_keep_their_subject(label, subject):
    assert topic(label) == subject
    assert critical_question(label) or subject == "referral"


def test_a_recorded_debarment_fact_supports_a_debarment_question():
    label = "Please indicate whether you have been declared ineligible to contract with any agency of the U.S. Government."
    assert topic(label) == "government"
    assert relevant_fact(label, "debarment_proceedings_pending")
