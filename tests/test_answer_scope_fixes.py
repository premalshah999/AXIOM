from types import SimpleNamespace

from jobpilot.answers import Resolver
from jobpilot.browser import BrowserEngine
from jobpilot.knowledge import relevant_fact, topic
from jobpilot.schemas import Answer, Profile

# The resolver's refusal for a saved source that no offered option matches.
UNOFFERED_SOURCE = "The saved application source is not in the observed catalog"
AGE_PARENTHETICAL = (
    "Are you at least 18 years or older? (If no, you may be required to provide authorization to work):"
)
DISCIPLINARY = (
    "Within the last three (3) years, have you been subject to any disciplinary action "
    "related to any professional license?"
)


def field(label, **extra):
    return {
        "id": "q",
        "label": label,
        "type": "radio",
        "required": True,
        "options": ["Yes", "No"],
        "value": "",
        "valid": True,
        **extra,
    }


class FakeDB:
    def __init__(self):
        self.events = []

    def event(self, run_id, kind, message, data):
        self.events.append((run_id, kind, data))


# ----- scope classification -------------------------------------------------------------------


def test_age_question_is_age_not_authorization():
    assert topic(AGE_PARENTHETICAL) == "age"
    assert topic("Are you age 18 or over?") == "age"
    assert topic("Are you authorized to work in the United States?") == "authorization"


def test_age_fact_supports_age_question_only():
    assert relevant_fact(AGE_PARENTHETICAL, "over_18")
    assert not relevant_fact(AGE_PARENTHETICAL, "work_authorized_us")


def test_disciplinary_license_question_is_not_a_referral():
    assert topic(DISCIPLINARY) != "referral"
    assert (
        topic("Do you have any related person's currently employed with the Vizient family of companies?")
        == "referral"
    )
    assert topic("Are you related to any employee of this company?") == "referral"


# ----- age answers ----------------------------------------------------------------------------


def test_local_answers_age_or_over_from_confirmed_fact(service):
    resolver = Resolver(
        Profile(facts={"over_18": "Yes (confirmed by applicant)"}), service.db, service.config
    )
    answer = resolver.local(field("Are you age 18 or over?"))
    assert answer.disposition == "answer" and answer.value == "Yes"
    assert answer.evidence_ids == ["fact:over_18"]


def test_validation_accepts_yes_cited_to_over_18_for_age_parenthetical(service):
    resolver = Resolver(Profile(), service.db, service.config)
    # Without the deterministic cross-check, only the scope rule decides this citation.
    resolver.local = lambda f: None
    question = field(AGE_PARENTHETICAL)
    answer = Answer(field_id="q", disposition="answer", value="Yes", evidence_ids=["fact:over_18"])
    result = resolver._validate_answer(question, answer, {"fact:over_18": True}, {}, {})
    assert result.disposition == "answer" and result.value == "Yes"


# ----- language -------------------------------------------------------------------------------


def test_language_row_reads_profile_languages(service):
    resolver = Resolver(Profile(languages=["English"]), service.db, service.config)
    question = field(
        "Language", type="dropdown", options=["English", "Spanish"], group="Language 1", required=False
    )
    answer = resolver.language_answer(question)
    assert answer.disposition == "answer" and answer.value == "English"
    assert answer.evidence_ids == ["profile:languages"]


def test_language_row_reads_language_proficiencies_fact(service):
    profile = Profile(facts={"language_proficiencies": {"English": "Complete proficiency"}})
    resolver = Resolver(profile, service.db, service.config)
    question = field(
        "Language", type="dropdown", options=["English", "Spanish"], group="Language 1", required=False
    )
    answer = resolver.language_answer(question)
    assert answer.disposition == "answer" and answer.value == "English"
    assert answer.evidence_ids == ["fact:language_proficiencies"]


# ----- unoffered saved source -----------------------------------------------------------------

VIZIENT_OPTIONS = ["Select One", "Displaced (US)", "LOA - Job Search (US)"]


def test_unoffered_saved_source_is_refused_by_resolver(service):
    resolver = Resolver(Profile(application_source="LinkedIn"), service.db, service.config)
    question = field("How Did You Hear About Us?", type="dropdown", options=VIZIENT_OPTIONS)
    answer = resolver.local(question)
    assert answer.disposition == "review"
    assert answer.blocker_type == "control_failure"
    assert answer.reason.startswith(UNOFFERED_SOURCE)


def test_offered_job_board_source_is_still_answered(service):
    resolver = Resolver(Profile(application_source="LinkedIn"), service.db, service.config)
    question = field(
        "How Did You Hear About Us?", type="dropdown", options=["Job Boards", "Social Media", "Other"]
    )
    answer = resolver.local(question)
    assert answer.disposition == "answer" and answer.value == "Job Boards"


def test_first_refusal_for_unoffered_source_ends_the_browser_loop():
    choose = {"label": "How Did You Hear About Us?", "type": "dropdown", "section": "", "group": ""}
    outcome = {
        "failure_category": "unsupported",
        "detail": "Answer validation rejected this choice: "
        + UNOFFERED_SOURCE
        + "; inspect its categories or search",
    }
    verdict = BrowserEngine.answer_verdict(SimpleNamespace(), None, choose, outcome, {})
    assert verdict and verdict["state"] == "waiting_browser"
    assert verdict["blocker_type"] == "control_failure"
    assert "owner review" in verdict["reason"]


def test_other_answer_rejections_still_need_two_before_stopping():
    target = {"label": "Field of Study", "type": "dropdown", "section": "", "group": ""}
    outcome = {
        "failure_category": "unsupported",
        "detail": "Answer validation rejected this choice: unoffered_choice",
    }
    rejected = {}
    assert BrowserEngine.answer_verdict(SimpleNamespace(), None, target, outcome, rejected) is None
    assert (
        BrowserEngine.answer_verdict(SimpleNamespace(), None, target, outcome, rejected)["state"]
        == "waiting_browser"
    )


# ----- diagnostics on the validation event ----------------------------------------------------


def test_answer_validation_event_records_proposed_value_and_options(service):
    db = FakeDB()
    resolver = Resolver(Profile(), db, service.config, run_id="run-1")
    options = [f"Option {i}" for i in range(20)]
    question = field("Field of Study", type="dropdown", options=options)
    answer = Answer(field_id="q", disposition="answer", value="Data Science", evidence_ids=["fact:education"])
    result = resolver._validate_answer(question, answer, {"fact:education": "Data Science"}, {}, {})
    assert result.disposition == "review"
    ((run_id, kind, data),) = db.events
    assert run_id == "run-1" and kind == "answer_validation"
    assert data["cause"] == "unoffered_choice"
    assert data["proposed"] == "Data Science"
    assert data["options"] == options[:12]


def test_internal_only_source_catalog_closes_the_posting():
    from jobpilot.answers import UNOFFERED_SOURCE as REFUSAL

    outcome = {
        "failure_category": "unsupported",
        "detail": "Answer validation rejected this choice: " + REFUSAL,
    }
    internal = {
        "label": "How Did You Hear About Us?",
        "type": "dropdown",
        "options": ["Displaced (US)", "LOA - Job Search (US)"],
    }
    verdict = BrowserEngine.answer_verdict(SimpleNamespace(), None, internal, outcome, {})
    assert verdict["state"] == "closed" and "Internal-only" in verdict["reason"]
    # An "Other" choice is open to external applicants: wait for review instead of closing.
    mixed = {**internal, "options": ["Employee", "Other"]}
    assert (
        BrowserEngine.answer_verdict(SimpleNamespace(), None, mixed, outcome, {})["state"]
        == "waiting_browser"
    )


def test_twice_rejected_question_goes_to_the_owner_instead_of_stalling():
    target = {
        "id": "f1",
        "label": "Select the programming languages with which you have three or more years of experience",
        "type": "checkboxgroup",
        "required": True,
        "options": ["Python", "Java", "C#"],
        "section": "",
        "group": "",
    }
    outcome = {
        "failure_category": "unsupported",
        "detail": "Answer validation rejected this choice: unsupported_evidence",
    }
    rejected = {}
    assert BrowserEngine.answer_verdict(SimpleNamespace(), None, target, outcome, rejected) is None
    verdict = BrowserEngine.answer_verdict(SimpleNamespace(), None, target, outcome, rejected)
    assert verdict["state"] == "needs_review" and verdict["blocker_type"] == "missing_fact"
    (review,) = verdict["reviews"]
    assert (
        review["question"] == target["label"]
        and review["options"] == ["Python", "Java", "C#"]
        and review["key"]
    )
