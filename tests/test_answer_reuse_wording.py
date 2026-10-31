"""The owner's answer is reused for the same question however a form words it, and for nothing that differs in a qualifier.

Owner 2026-10-31: "A lot of needs answer applications are just stuck through the page", "never ask me what you can
answer". The audit found 70 of 724 owner questions asked again after an identical one was answered. Reuse stays exact
about what is asked: only case, spacing, punctuation, a "Required" marker, a question number and the employer's own
name are ignored. A false match (a "U.S. citizen" answer given to "authorized to work") is worse than a second question."""

import pytest

from jobpilot import owner_questions as oq
from jobpilot.db import Review
from jobpilot.knowledge import KnowledgeBase, kb_norm, save
from jobpilot.schemas import Profile, ReviewedAnswer

from test_owner_questions_20261023 import add_job, add_review


def kb_with(*entries):
    return KnowledgeBase(None, Profile(reviewed_answers=[ReviewedAnswer(id=f"e{i}", **e) for i, e in enumerate(entries)]))


@pytest.mark.parametrize(
    "asked,saved,employer",
    [
        ("ARE YOU LEGALLY AUTHORIZED TO WORK IN THE U.S.? *", "Are you legally authorized to work in the U.S.?", ""),
        ("1. Are you legally authorized to work in the U.S.? (Required)", "Are you legally authorized to work in the U.S.?", ""),
        ("Q3: Are you legally authorized to work in the U.S.? Required", "Are you legally authorized to work in the U.S.?", ""),
        ("* Are you willing to relocate:", "Are you willing to relocate?", ""),
        ("13 - Do you have a valid driver's license?", "Do you have a valid drivers license", ""),
        ("Racial/ethnic background (mark all that apply)", "racial ethnic background - mark all that apply", ""),
        ("Do you have a full-time offer?  ", "Do you have a full time offer?", ""),
        ("How many years of Python do you have? ", "How many years of Python do you have", ""),
        ("Have you ever worked for Acme Corp?", "Have you ever worked for Acme?", "Acme Corp"),
        ("Do you have 3.5 years of experience?", "Do you have 3.5 years of experience", ""),
    ],
)
def test_the_same_question_in_other_dress_has_one_identity(asked, saved, employer):
    assert kb_norm(asked, employer) == kb_norm(saved, employer)


@pytest.mark.parametrize(
    "asked,saved",
    [
        ("Are you a U.S. citizen?", "Are you authorized to work in the U.S.?"),
        ("Have you worked for the government in the last 5 years?", "Have you ever worked for the government?"),
        ("Do you have fewer than 5 years of experience?", "Do you have more than 5 years of experience?"),
        ("Years of experience < 5?", "Years of experience > 5?"),
        ("Do you have 3-5 years of experience?", "Do you have 5 years of experience?"),
        ("3 - 5 years of experience in Python?", "5 years of experience in Python?"),
        ("Do you have 1.5 years of experience?", "Do you have 15 years of experience?"),
        ("Are you NOT authorized to work in the U.S.?", "Are you authorized to work in the U.S.?"),
        ("Will you not require sponsorship?", "Will you require sponsorship?"),
        ("Do you know C#?", "Do you know C?"),
        ("Have you ever worked for Globex?", "Have you ever worked for Initech?"),
    ],
)
def test_questions_that_differ_in_a_qualifier_never_share_an_identity(asked, saved):
    assert kb_norm(asked, "Acme") != kb_norm(saved, "Acme")


def test_an_answer_is_reused_across_wordings_and_employers():
    kb = kb_with(
        {"question": "Are you willing to relocate?", "answer": "No", "options": ["Yes", "No"], "employer": "Acme",
         "scope": "personal"},
    )
    found = kb.lookup("2) ARE YOU WILLING TO RELOCATE? * Required", ["Select...", "Yes", "No"], "Initech", "radio")
    assert found and found[1] == "No"
    assert oq.reusable_answer(kb, "Are you willing to relocate:", ["Yes", "No"], {}, "", "Initech") == "No"


def test_a_choice_is_reused_only_when_the_saved_answer_is_one_of_the_options_offered():
    kb = kb_with({"question": "What is your expected salary range?", "answer": "85000", "employer": "Acme",
                  "scope": "personal"},
                 {"question": "Which gender do you identify with?", "answer": "Male", "employer": "Acme",
                  "scope": "personal"})
    # "85000" was once mapped to the offered "$85,000" and the nearest bracket: a guess at a different answer.
    assert kb.lookup("What is your expected salary range?", ["$85,000", "$90,000"], "Initech", "select") is None
    assert kb.lookup("Which gender do you identify with?", ["Female", "Non-binary"], "Initech", "select") is None
    assert kb.lookup("Which gender do you identify with?", ["Female", "Male"], "Initech", "select")[1] == "Male"
    # A menu whose options were never read cannot confirm a saved choice.
    assert kb.lookup("Which gender do you identify with?", [], "Initech", "combobox") is None


def test_an_answer_given_to_one_employer_stays_with_it():
    kb = kb_with(
        {"question": "Have you ever worked for Acme?", "answer": "Yes", "options": ["Yes", "No"], "employer": "Acme",
         "scope": "employer"},
        {"question": "Why do you want to work here?", "answer": "The mission", "employer": "", "scope": "employer"},
    )
    assert kb.lookup("Have you ever worked for Acme?", ["Yes", "No"], "Acme", "radio")[1] == "Yes"
    assert kb.lookup("Have you ever worked for Initech?", ["Yes", "No"], "Initech", "radio") is None
    assert kb.lookup("Have you ever worked for Acme?", ["Yes", "No"], "Initech", "radio") is None
    # Two postings with no employer name are not one employer.
    assert kb.lookup("Why do you want to work here?", [], "", "textarea") is None


def test_a_bare_caption_keeps_matching_only_the_same_caption():
    kb = kb_with({"question": "End Date", "answer": "05/2020", "employer": "Acme", "scope": "personal"},
                 {"question": "Please specify", "answer": "NYU", "employer": "Acme", "scope": "personal"})
    assert kb.lookup("End Date", [], "Initech", "text")[1] == "05/2020"
    assert kb.lookup("1. End Date:", [], "Initech", "text") is None
    assert kb.lookup("Please specify:", [], "Initech", "text") is None


async def test_open_questions_are_settled_by_an_answer_worded_differently(service):
    run = add_job(service, "sweep")
    save(service.db, "Are you willing to relocate?", "No", ["Yes", "No"], "Acme", "personal")
    save(service.db, "Have you worked for the government in the last 5 years?", "No", ["Yes", "No"], "Acme", "personal")
    add_review(service, "same", run, "3. Are you willing to relocate? *Required", ["Select...", "Yes", "No"])
    add_review(service, "qualified", run, "Have you ever worked for the government?", ["Yes", "No"])
    add_review(service, "unlisted", run, "Are you willing to relocate?", ["Maybe", "Yes, within the state"],
               key="answers-v9:unlisted")
    assert await service.settle_open_questions() == 1
    with service.db.session() as s:
        assert s.get(Review, "same").answer == "No"
        assert s.get(Review, "qualified").answer is None  # "last 5 years" is not "ever"
        assert s.get(Review, "unlisted").answer is None  # "No" is not among the options
