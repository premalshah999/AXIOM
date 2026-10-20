"""Capital One 2026-10-14: a question that defines Yes for OPT support is answered Yes for an OPT applicant."""

import pytest

from jobpilot.answer_context import status_defined_answer
from jobpilot.answers import Resolver
from jobpilot.schemas import Profile

CAPITAL_ONE = (
    "Will you, now or in the future, require any immigration related support or sponsorship from Capital One "
    "to maintain U.S. work authorization? Please note, immigration related support does include documentation "
    "needed for F-1 OPT, F-1 STEM OPT, F-1 CPT and J-1. If you require this type of support, your answer to "
    "this question will be “Yes.” Visas that will require sponsorship include H-1B, TN, H-1B1, E-3."
)
FACTS = {
    "requires_future_sponsorship_US": False,
    "current_immigration_status_US": "OPT; expiration June 2030",
}


def test_defined_yes_for_listed_status():
    facts = {k.lower(): v for k, v in FACTS.items()}
    assert status_defined_answer(CAPITAL_ONE, facts) == (True, ["fact:current_immigration_status_US"])
    # Not listed, or no definition: the ordinary sponsorship fact decides.
    assert status_defined_answer(CAPITAL_ONE.replace("F-1 OPT, F-1 STEM OPT, ", ""), facts) is None
    assert status_defined_answer("Will you require visa sponsorship?", facts) is None
    citizen = {"current_immigration_status_us": "US citizen"}
    assert status_defined_answer(CAPITAL_ONE, citizen) is None


def test_resolver_answers_yes_and_validator_accepts_it(service):
    r = Resolver(Profile(facts=FACTS), service.db, service.config)
    field = {
        "id": "q",
        "label": CAPITAL_ONE,
        "type": "select",
        "required": True,
        "options": ["Yes", "No"],
        "value": "",
        "valid": True,
    }
    answer = r._intent_answer(field, CAPITAL_ONE.lower())
    assert answer.value == "Yes" and answer.evidence_ids == ["fact:current_immigration_status_US"]
    facts, evidence, learned = r.answer_sources()
    assert r._validate_answer(field, answer, facts, evidence, learned).disposition == "answer"


# The question lists the statuses it excludes as well as those it defines (review of the answer validation, 2026-10-20):
# "other than F-1 OPT" was read as naming F-1 OPT, so an OPT applicant was told to answer Yes.
OPT = {"current_immigration_status_us": "F-1 OPT"}


@pytest.mark.parametrize(
    "label",
    [
        'Select "Yes" if you hold a work visa other than F-1 OPT.',
        'Select "Yes" if you need sponsorship (excluding F-1 OPT and CPT).',
        'Answer "Yes" if you hold any nonimmigrant visa, except OPT.',
    ],
)
def test_a_status_the_question_excludes_does_not_define_the_answer(label):
    assert status_defined_answer(f"Will you require sponsorship? {label}", OPT) is None


def test_a_status_the_question_lists_still_defines_the_answer():
    label = 'Will you require sponsorship? Select "Yes" if you hold a visa such as H-1B or F-1 OPT.'
    assert status_defined_answer(label, OPT) == (True, ["fact:current_immigration_status_US"])
