"""Requiring a relocation package is a different fact from being willing to relocate."""

import pytest

from jobpilot.answers import Resolver
from jobpilot.schemas import Profile


@pytest.fixture
def resolver(service):
    def make(**facts):
        return Resolver(Profile(facts={"willing_to_relocate": True, **facts}), service.db, service.config)

    return make


def field(label):
    return {"id": "f", "label": label, "type": "radio", "required": True, "options": ["Yes", "No"]}


@pytest.mark.parametrize(
    "label",
    [
        "Do you require any relocation assistance?",
        "Will you need relocation support to join us?",
        "Would you require a relocation package?",
    ],
)
def test_assistance_is_not_willingness(resolver, label):
    ans = resolver(relocation_at_own_expense=True)._intent_answer(field(label), label.lower())
    assert ans is not None and ans.value == "No"


def test_assistance_without_own_expense_fact_is_not_guessed(resolver):
    label = "Do you require any relocation assistance?"
    assert resolver()._intent_answer(field(label), label.lower()) is None


def test_willingness_still_answers_yes(resolver):
    label = "Are you willing to relocate?"
    assert resolver()._intent_answer(field(label), label.lower()).value == "Yes"
