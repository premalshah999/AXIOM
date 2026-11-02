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
