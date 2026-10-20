"""Bunge (SuccessFactors) 2026-10-16 offered "I dont wish to answer"; the missing apostrophe hid the decline
choice, so the owner was asked to disclose a disability status instead of the standing decline policy."""

from jobpilot.answers import DECLINE, Resolver
from jobpilot.schemas import Profile

OPTIONS = ["I dont wish to answer", "No I dont have a disability", "Yes I have a disability or previously had a disability"]


def test_decline_choice_is_recognized_without_an_apostrophe():
    assert DECLINE.search("I dont wish to answer") and DECLINE.search("I don't wish to answer")
    assert DECLINE.search("I dont want to answer")
    assert not DECLINE.search(OPTIONS[1]) and not DECLINE.search(OPTIONS[2])


def test_disability_question_takes_the_decline_choice(service):
    resolver = Resolver(Profile(), service.db, service.config, job={"ats": "successfactors", "url": "https://example.test/job"})
    field = {
        "id": "d",
        "label": "Disability",
        "type": "combobox",
        "required": True,
        "value": "",
        "valid": True,
        "group": "",
        "section": "",
        "options": OPTIONS,
    }
    answer = resolver.local(field)
    assert answer and answer.disposition == "answer" and answer.value == "I dont wish to answer"
