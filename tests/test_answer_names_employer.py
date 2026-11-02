"""One employer's answer never goes to another (2026-11-02 safety review).

"Tell us why you want to work here" stayed per-employer once, and after a merge an answer naming Acme went to the next employer's open
question, because the check read the question's words and never the answer's. What makes an answer one employer's is what it says as much
as what was asked. The other half: a plain answer still clears every job asking the same thing. Synthetic data only."""

import pytest
from sqlalchemy import select

from jobpilot.db import Review, Setting
from jobpilot.knowledge import KnowledgeBase, applicable, learned_answer
from jobpilot.owner_questions import employer_only, reusable_answer
from jobpilot.schemas import ReviewedAnswer

from test_owner_questions_20261023 import add_job, add_review

pytestmark = pytest.mark.integration


def control(service):
    with service.db.exclusive() as s:
        row = s.get(Setting, "control")
        row.value = {"paused": False, "auto_submit": True}


def waiting(service, name, company, question, options=(), context=None):
    run_id = add_job(service, name, company=company, created="2026-10-30T00:00:00")
    add_review(service, "q-" + name, run_id, question, options, key="answers-v9:" + name, context=context or {})
    return run_id


def answer_of(service, review_id):
    with service.db.session() as s:
        return s.get(Review, review_id).answer


@pytest.mark.parametrize(
    "question",
    [
        "Tell us why you want to work here",
        "Tell us why you want to work here.",
        "Why would you like to join us?",
        "Please explain why you are interested in working at the company.",
        "Why do you want this job?",
        "What interests you about working with us?",
        "What attracts you to this opportunity?",
        "Describe what attracted you to this opportunity",
        "Tell us why we should hire you",
        "Why should we hire you?",
        "What motivated you to apply?",
        "What is your motivation for applying?",
        "What about the company appeals to you?",
        "What makes you a good fit?",
        "Reason for applying",
        "Cover letter",
    ],
)
def test_a_motive_question_is_one_employers_however_it_is_worded(question):
    assert employer_only(question)


@pytest.mark.parametrize(
    "question,options",
    [
        ("When can you start?", ()),
        ("Describe your experience with Python.", ()),
        ("Why did you leave your last position?", ()),  # About the applicant's past: the same answer anywhere.
        ("Explain why you left school.", ()),
        ("Are you at least 18 years old?", ("Yes", "No")),
        ("Are you legally authorized to work here?", ("Yes", "No")),
    ],
)
def test_a_question_about_the_applicant_is_not_tied_to_one_employer(question, options):
    assert not employer_only(question, options)


def test_an_answer_naming_acme_never_reaches_the_next_employers_open_question(client, service):
    control(service)
    question = "Tell us why you want to work here"
    waiting(service, "a", "Acme", question, (), {"type": "textarea"})
    waiting(service, "b", "Birch Labs", question, (), {"type": "textarea"})
    assert client.post("/api/reviews/q-a", json={"answer": "I admire how Acme ships small tools."}).status_code == 200
    assert answer_of(service, "q-b") is None


def test_the_answers_own_words_tie_it_even_when_the_question_could_be_anyones(client, service):
    """The wording doesn't say so, and the question would be shared. The answer names the employer it was written for."""
    control(service)
    question = "Describe your approach to testing."
    waiting(service, "a", "Acme", question, (), {"type": "textarea"})
    waiting(service, "b", "Birch Labs", question, (), {"type": "textarea"})
    waiting(service, "c", "Cedar Inc", question, (), {"type": "textarea"})
    assert client.post("/api/reviews/q-a", json={"answer": "At Acme I would pair on every change."}).status_code == 200
    assert answer_of(service, "q-b") is None and answer_of(service, "q-c") is None


def test_the_answer_goes_to_the_same_employers_other_job(client, service):
    control(service)
    question = "Tell us why you want to work here"
    waiting(service, "a", "Acme", question, (), {"type": "textarea"})
    waiting(service, "b", "Acme", question, (), {"type": "textarea"})
    waiting(service, "c", "Birch Labs", question, (), {"type": "textarea"})
    answer = "I admire how Acme ships small tools."
    assert client.post("/api/reviews/q-a", json={"answer": answer}).status_code == 200
    assert answer_of(service, "q-b") == answer and answer_of(service, "q-c") is None


def test_a_plain_answer_still_clears_every_job_asking_the_same_thing(client, service):
    control(service)
    question = "What is your earliest start date?"
    for name, company in (("a", "Acme"), ("b", "Birch Labs"), ("c", "Cedar Inc"), ("d", "Dune")):
        waiting(service, name, company, question, (), {"type": "text"})
    assert client.post("/api/reviews/q-a", json={"answer": "January 15, 2027"}).status_code == 200
    assert {answer_of(service, "q-" + name) for name in "bcd"} == {"January 15, 2027"}


def test_a_plain_choice_still_clears_every_job_asking_the_same_thing(client, service):
    control(service)
    question = "Are you willing to relocate?"
    waiting(service, "a", "Acme", question, ("Yes", "No"))
    waiting(service, "b", "Birch Labs", question, ("No", "Yes"))
    assert client.post("/api/reviews/q-a", json={"answer": "No"}).status_code == 200
    assert answer_of(service, "q-b") == "No"


def test_a_saved_answer_that_names_its_employer_is_kept_with_it():
    saved = learned_answer("r1", "Which city do you live in?", "Near Acme headquarters", [], "Acme")
    assert saved["scope"] == "employer"
    plain = learned_answer("r2", "Which city do you live in?", "Boston", [], "Acme")
    assert plain["scope"] == "personal"


@pytest.mark.parametrize("question", ["What motivated you to apply?", "Why should we hire you?", "Describe what attracted you to this opportunity"])
def test_why_this_job_is_saved_for_one_employer_even_saved_earlier_as_anyones(question):
    assert learned_answer("r1", question, "Your fraud-detection work drew me.", [], "Acme")["scope"] == "employer"
    old = ReviewedAnswer(id="r", question=question, answer="Your fraud-detection work drew me.", options=[],
                         employer="Acme", scope="personal", layer="fact")
    assert applicable(old, "Acme") and not applicable(old, "Birch Labs")


def test_an_answer_saved_before_this_rule_still_stays_with_its_employer():
    """Saved as "personal" by the earlier code: what it says decides, so an old row can't reach another employer either."""
    old = ReviewedAnswer(id="r", question="Which city do you live in?", answer="Near Acme headquarters", options=[],
                         employer="Acme", scope="personal", layer="fact")
    assert applicable(old, "Acme") and not applicable(old, "Birch Labs")
    plain = ReviewedAnswer(id="p", question="Which city do you live in?", answer="Boston", options=[],
                           employer="Acme", scope="personal", layer="fact")
    assert applicable(plain, "Acme") and applicable(plain, "Birch Labs")


async def test_a_later_sweep_of_open_questions_never_carries_it_either(client, service):
    control(service)
    question = "Tell us why you want to work here"
    waiting(service, "a", "Acme", question, (), {"type": "textarea"})
    waiting(service, "b", "Birch Labs", question, (), {"type": "textarea"})
    assert client.post("/api/reviews/q-a", json={"answer": "I admire how Acme ships small tools."}).status_code == 200
    await service.settle_open_questions()
    assert answer_of(service, "q-b") is None
    assert reusable_answer(KnowledgeBase(service.db), question, (), {"type": "textarea"}, "", "Birch Labs") is None
    with service.db.session() as s:
        assert s.scalar(select(Review.answer).where(Review.id == "q-a")) == "I admire how Acme ships small tools."
