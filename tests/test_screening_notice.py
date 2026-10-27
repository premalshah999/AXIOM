"""Aptiv (Workday) 2026-10-27: a restricted-party-screening notice the applicant acknowledges was read as a work
authorization question; the accept-all-terms acknowledgement was refused and the owner was asked."""

from jobpilot.answers import Resolver
from jobpilot.answers.consent import EMBEDDED_DISCLOSURE
from jobpilot.knowledge import topic
from jobpilot.schemas import Answer, Profile

LABEL = ("Restricted Party Screening Aptiv/Windriver will be conducting restricted party screening as required by law and "
         "company policy for this position. I understand my application information will be checked against government "
         "watchlists and sanctions databases, and that this screening is mandatory for consideration. I confirm my "
         "application information is accurate and acknowledge that screening results may affect my employment eligibility.")


def test_a_screening_notice_is_a_background_check_not_a_disclosure():
    assert topic(LABEL) == "background_check"
    assert not EMBEDDED_DISCLOSURE.search(LABEL)
    assert EMBEDDED_DISCLOSURE.search("Have you ever been subject to sanctions by a regulator?")


def test_the_acknowledgement_is_accepted_under_the_terms_policy(service):
    r = Resolver(Profile(first_name="Alex", last_name="Doe", accept_all_application_terms=True), service.db,
                 service.config, job={"ats": "workday", "url": "https://x.example.test"})
    q = {"id": "q", "label": LABEL, "type": "dropdown", "required": True, "options": ["Yes", "No"], "value": "",
         "valid": True}
    a = Answer(field_id="q", disposition="answer", value="Yes", evidence_ids=["fact:application_consent_policy"])
    assert r._validate_answer(q, a, {"fact:application_consent_policy": True}, {}, {}).value == "Yes"
