"""Regions (2026-11-01): "Did you complete the WOTC survey?" was answered Yes, which asked for a confirmation number
nobody has. The application never completes the employer's external survey, so the truthful answer is No."""

from jobpilot.answers import Resolver
from jobpilot.schemas import Profile

NOTICE = (
    "Our company is participating in a Federal initiative called the Work Opportunity Tax Credit (WOTC) program. "
    "When you complete the online survey, you will be provided with a confirmation number. Did you complete the WOTC survey? *"
)


def field(label):
    return {"id": "w", "label": label, "type": "dropdown", "options": ["Yes", "No"], "required": True,
            "section": "", "group": "", "value": "", "valid": True}


def test_survey_completion_is_no(service):
    resolver = Resolver(Profile(), service.db, service.config, job={"ats": "workday", "url": "https://example.test/j"})
    for label in (NOTICE, "Did you complete the WOTC survey?"):
        answer = resolver.local(field(label))
        assert answer.value == "No" and answer.evidence_ids == ["policy:wotc-survey-not-completed"]


def test_other_questions_about_the_survey_are_untouched(service):
    resolver = Resolver(Profile(), service.db, service.config, job={"ats": "workday", "url": "https://example.test/j"})
    assert resolver.local(field("Would you like to complete the WOTC survey?")) is None


def test_a_survey_this_run_finished_is_yes_and_one_it_only_launched_is_left_to_the_person(service):
    resolver = Resolver(Profile(), service.db, service.config, job={"ats": "workday", "url": "https://example.test/j"})
    label = "Did you complete the WOTC survey?"
    resolver.survey = "finished"
    assert resolver.local(field(label)).value == "Yes"
    resolver.survey = "launched"
    answer = resolver.local(field(label))
    assert answer.disposition == "review" and answer.value in (None, "")
    resolver.survey = ""
    assert resolver.local(field(label)).value == "No"


def test_the_form_session_records_the_survey_outcome_on_its_resolver(service):
    from jobpilot.forms import FormSession

    resolver = Resolver(Profile(), service.db, service.config, job={})
    session = FormSession(None, resolver, None, lambda *a, **k: None)
    session.note_survey("launched")
    assert resolver.survey == "launched"
    session.note_survey("finished")
    session.note_survey("launched")  # a second launch never erases a finished survey
    assert resolver.survey == "finished"
