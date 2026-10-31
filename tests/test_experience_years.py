"""Apptronik (Greenhouse, 2026-10-30): "Do you have an MS with 3+ years of relevant experience, or a recent PhD, in
Robotics, CS, EE, or a related field?" was answered Yes six times and No twice for one profile, and the Yes cited only the
attestation that the work history is complete. A years claim is arithmetic on the work history; it is computed, never
recalled. The applicant here is synthetic."""

from datetime import date

import pytest

from jobpilot.answers import Resolver
from jobpilot.answers.experience import COMPUTED, evaluate, work_months
from jobpilot.schemas import Answer, Profile, ReviewedAnswer

ASOF = date(2026, 10, 30)


def row(title, start, end="", current=False):
    return {"company": "ExampleSoft", "title": title, "start": start, "end": end, "current": current}


# Two overlapping full-time rows (2021-01 to 2024-07, the overlap counted once) and a separate internship.
WORK = [row("Software Engineer", "2021-01", "2023-01"), row("Software Engineer", "2022-07", "2024-07")]
INTERN = row("Software Engineering Intern", "2020-06", "2020-12")
MASTERS = {"degree": "Master of Science", "field_of_study": "Computer Science", "end": "2020-05"}


def verdict(question, work=WORK, education=(MASTERS,)):
    return evaluate(question, list(work), list(education), ASOF)


def test_months_are_the_union_of_the_rows_and_an_internship_counts_only_in_the_wider_figure():
    (lo, hi), (pro_lo, pro_hi), complete, rows = work_months(WORK + [INTERN], ASOF)
    assert (pro_lo, pro_hi) == (42, 43), "the 6 overlapping months count once"
    assert (lo, hi) == (48, 50) and complete and rows == [0, 1, 2]
    # A current row runs to today; a row that starts after today adds nothing; an undated one makes the figure partial.
    (lo, _), _, complete, _ = work_months([row("Engineer", "2026-01", current=True), row("Engineer", "2027-01", "2028-01")], ASOF)
    assert lo == 9 and complete
    assert work_months([row("Engineer", "")], ASOF)[2] is False


def test_a_generic_years_question_is_settled_from_the_months():
    assert verdict("Do you have 3+ years of experience?").result is True
    assert verdict("Do you have at least 5 years of work experience?").result is False
    assert verdict("Do you have more than 3 years of relevant experience?").result is True
    assert verdict("Do you have 4+ years of experience?", WORK + [INTERN]).result is True, "internships are not excluded"
    # "Professional", "full-time" and "industry" exclude internships: 42 to 43 months are fewer than 48.
    assert verdict("Do you have 4+ years of professional experience?", WORK + [INTERN]).result is False
    assert verdict("Do you have 4+ years of full-time experience?", WORK + [INTERN]).result is False
    assert verdict("Do you have 3+ years of professional experience?", WORK + [INTERN]).result is True


def test_a_compound_evaluates_each_clause():
    plain = "Do you have an MS with 3+ years of relevant experience, or a recent PhD?"
    assert verdict(plain).result is True
    assert verdict(plain, education=[]).result is False, "no master's and no PhD"
    assert verdict(plain, work=[row("Software Engineer", "2025-01", "2026-01")]).result is False, "12 months"
    # A PhD is held but how recent is "recent" is the employer's call.
    phd = {"degree": "PhD", "field_of_study": "Physics", "end": "2018-05"}
    assert verdict(plain, education=[phd], work=[row("Engineer", "2025-01", "2026-01")]).result is None
    assert verdict("Do you have a Master's degree and 2+ years of experience?", education=[]).result is False
    # The degree's own field is read: Computer Science is named, Physics is not (and "related" is a person's judgement).
    named = "Do you have a Master's degree in Computer Science and 2+ years of experience?"
    assert verdict(named).result is True
    assert verdict(named, education=[{**MASTERS, "field_of_study": "Physics"}]).result is None
    # A master's degree not yet awarded is not held.
    pending = {**MASTERS, "end": "2027-05"}
    assert verdict("Do you have a Master's degree and 2+ years of experience?", education=[pending]).result is False


def test_the_apptronik_question_stays_undecided_but_cannot_be_a_yes_the_history_rules_out():
    question = ("Do you have an MS with 3+ years of relevant experience, or a recent PhD, in Robotics, CS, EE, or a "
                "related field?")
    undecided = verdict(question)
    assert undecided.result is None and undecided.possible is True
    short = verdict(question, work=[row("Software Engineer", "2025-01", "2026-01")])
    assert short.result is None and short.possible is False, "12 months cannot be 3 years in any specialty"
    # A named specialty is the same: the profile records durations, not which row was spent on what.
    assert verdict("Do you have 2+ years of experience with Python?").result is None
    assert verdict("Do you have 10+ years of experience with Python?").possible is False


@pytest.mark.parametrize("question", [
    "Are you at least 18 years of age?",
    "Have you worked for the government in the past 3 years?",
    "Do you have experience with MS Office?",
    "Do you have between 3 and 5 years of experience?",
    "Do you have 2-5 years of experience?",
    "Do you have less than 2 years of experience?",
    "Do you have a Bachelor's degree or equivalent experience and 3 years of experience?",
    "Are you willing to relocate?",
])
def test_questions_that_are_not_a_length_of_experience_are_not_read(question):
    assert verdict(question) is None


def test_no_dated_work_row_is_not_proof_of_no_experience():
    assert verdict("Do you have 3+ years of experience?", work=[]) is None
    assert verdict("Do you have 3+ years of experience?", work=[row("Engineer", "")]) is None


# ----- the resolver ---------------------------------------------------------------------------

PAST = [{"company": "ExampleSoft", "title": "Software Engineer", "start": "2012-01", "end": "2016-01"}]  # 48 months
EDUCATION = [{"school": "Example University", "degree": "Master of Science", "field_of_study": "Computer Science",
              "end": "2011-05"}]


def field(label, options=("Yes", "No"), **extra):
    return {"id": "q", "label": label, "type": "radio", "required": True, "options": list(options), **extra}


def resolver(service, **profile):
    return Resolver(Profile(first_name="Alex", work=PAST, education=EDUCATION, **profile), service.db, service.config,
                    job={"ats": "greenhouse", "url": "https://example.test/j"})


def test_the_resolver_answers_a_settled_years_question_from_the_rows(service):
    answer = resolver(service).local(field("Do you have 3+ years of experience?"))
    assert answer.disposition == "answer" and answer.value == "Yes"
    assert answer.evidence_ids == ["profile:work.0"] and answer.reason.startswith(COMPUTED)
    no = resolver(service).local(field("Do you have 5+ years of experience?"))
    assert no.value == "No" and no.evidence_ids == ["profile:work.0"]
    compound = resolver(service).local(field("Do you have a Master's degree and 3+ years of experience?"))
    assert compound.value == "Yes" and compound.evidence_ids == ["profile:work.0", "profile:education.0"]
    # An undecided clause is the model's: nothing local answers it, and a free-text box is not a yes/no.
    assert resolver(service).local(field("Do you have 3+ years of experience with Python?")) is None
    assert resolver(service).experience_years_answer(field("Do you have 3+ years of experience?", options=())) is None


def test_a_saved_answer_that_contradicts_the_computed_one_loses(service):
    saved = ReviewedAnswer(id="r1", question="Do you have 5+ years of experience?", answer="Yes", scope="personal",
                           layer="fact", options=["Yes", "No"])
    answer = resolver(service, reviewed_answers=[saved]).local(field("Do you have 5+ years of experience?"))
    assert answer.value == "No" and answer.reason.startswith(COMPUTED)


def validate(service, value, evidence, question, **profile):
    r = resolver(service, **profile)
    facts = {"fact:employment_history_complete": "complete", "profile:work.0": PAST[0],
             "profile:education.0": EDUCATION[0], "fact:kb_education_1": "MS"}
    r.local = lambda f: None  # Only the validator's own rule is under test.
    answer = Answer(field_id="q", disposition="answer", value=value, evidence_ids=evidence)
    return r._validate_answer(field(question), answer, facts, {}, {})


UNDECIDED = "Do you have 2+ years of experience with Python?"


def test_a_yes_that_rests_on_the_attestation_or_an_education_row_alone_is_refused(service):
    for evidence in (["fact:employment_history_complete"], ["fact:kb_education_1", "profile:education.0"]):
        result = validate(service, "Yes", evidence, UNDECIDED)
        assert result.disposition != "answer" and result.reason == "unsupported_evidence"
    ok = validate(service, "Yes", ["profile:work.0"], UNDECIDED)
    assert ok.disposition == "answer" and ok.value == "Yes"
    # A No never needs evidence of experience, and a settled verdict refuses the opposite answer.
    assert validate(service, "No", ["fact:employment_history_complete"], UNDECIDED).disposition == "answer"
    assert validate(service, "No", ["profile:work.0"], "Do you have 3+ years of experience?").reason == "question_polarity"
    assert validate(service, "Yes", ["profile:work.0"], "Do you have 9+ years of experience?").reason == "question_polarity"
    # More years than the whole history holds rules out a Yes in any specialty.
    assert validate(service, "Yes", ["profile:work.0"], "Do you have 9+ years of experience with Python?").reason == "question_polarity"


async def test_a_verified_earlier_answer_does_not_keep_a_menu_value_the_history_contradicts(service):
    """The ledger reuse kept a combobox's earlier No for a Yes the work history supports (a menu was exempt from the
    check that a native control got)."""
    from types import SimpleNamespace

    from jobpilot.answer_context import digest
    from jobpilot.forms import FormSession

    question = "Do you have 3+ years of experience?"
    r = resolver(service)
    page_field = {**field(question), "type": "combobox", "value": "No", "valid": True, "error": "", "slot": 0,
                  "section": "", "group": "", "name": "", "automation_id": ""}
    form = FormSession.__new__(FormSession)
    form.resolver, form.control_choices, form.failed_strategies = r, {}, {}
    form.page = SimpleNamespace(url="https://boards.example.test/apply")
    form.expected, form.expected_labels, form.expected_slots, form.ledger = {}, {}, {}, {}
    form.quality_answer = lambda f: None
    form.catalog_answer = lambda f: None
    form.record_answer = lambda *args, **kwargs: None
    form.ledger["a"] = {
        "field_key": list(form.field_key(page_field)), "verified": True, "evidence_ids": ["fact:kb_x"], "answer": "No",
        "profile_digest": digest(r.profile.model_dump()),
        "question_digest": digest([form.question_shape(page_field), page_field.get("context", "")]),
    }
    assert FormSession.preselected(form, page_field, False) is False, "the stored No was kept"
    # The same stored answer for a question the history does not decide is still reused.
    other = {**page_field, "label": "Do you have 3+ years of experience with Python?"}
    form.ledger["a"].update(field_key=list(form.field_key(other)),
                            question_digest=digest([form.question_shape(other), other.get("context", "")]))
    assert FormSession.preselected(form, other, False) is True
