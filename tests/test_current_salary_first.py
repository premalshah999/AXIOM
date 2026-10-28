"""Koniag (Dayforce, 2026-10-28): "What is your desired compensation?" kept 80000, a reply saved for other employers'
salary questions before the owner's $90,000-$120,000 policy, against a current expectation of 105000. The current
expectation answers before saved replies. The applicant is synthetic."""

from jobpilot.answers import Resolver
from jobpilot.answers.text import answer_key
from jobpilot.schemas import Profile, ReviewedAnswer

QUESTION = "What is your desired compensation?"


def field(label=QUESTION):
    return {"id": "q", "label": label, "type": "text", "required": True, "value": "", "valid": True, "group": "",
            "section": "", "options": []}


def test_the_current_expectation_replaces_a_saved_older_amount(service):
    f = field()
    profile = Profile(salary_expectation="105000",
                      approved_answers={answer_key({**f, "employer": ""}): "80000"},
                      reviewed_answers=[ReviewedAnswer(id="r1", question=QUESTION, answer="80000", scope="personal")])
    answer = Resolver(profile, service.db, service.config).local(f)
    assert answer.disposition == "answer" and "105" in str(answer.value)


def test_current_salary_questions_keep_their_saved_answers(service):
    f = field("What is your current salary?")
    profile = Profile(salary_expectation="105000", approved_answers={answer_key({**f, "employer": ""}): "70000"})
    answer = Resolver(profile, service.db, service.config).local(f)
    assert answer is None or "105" not in str(answer.value)
