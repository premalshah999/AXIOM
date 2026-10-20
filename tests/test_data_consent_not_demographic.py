"""AbbVie 2026-10-14: consent to process sensitive data is answered by the consent policy, not declined."""

from jobpilot.answers import Resolver, demographic_topic
from jobpilot.schemas import Profile

ABBVIE = (
    "I consent to the collection, use, and disclosure of my sensitive personal data, including racial and "
    "ethnic origin information, as described in this candidate application, and as further described in "
    "the Job Application section of the AbbVie's Privacy Notice."
)


def test_data_consent_is_not_an_identity_question():
    assert demographic_topic(ABBVIE) is None
    assert demographic_topic("What is your race or ethnicity?") == "race"


def test_consent_checkbox_is_checked_under_accept_all_terms(service):
    r = Resolver(
        Profile(accept_all_application_terms=True, decline_demographics=True), service.db, service.config
    )
    f = {
        "id": "q",
        "label": ABBVIE,
        "type": "checkbox",
        "required": True,
        "options": [],
        "value": "",
        "valid": True,
    }
    answer = r.local(f)
    assert answer is not None and answer.disposition == "answer" and answer.value == "true"


def test_certification_next_to_ethnicity_consent_is_checked(service):
    r = Resolver(
        Profile(accept_all_application_terms=True, decline_demographics=True), service.db, service.config
    )
    f = {
        "id": "q2",
        "label": "I agree that everything submitted is truthful and accurate.",
        "type": "checkbox",
        "required": True,
        "options": [],
        "value": "",
        "valid": True,
        "peers": ABBVIE,
    }
    answer = r.local(f)
    assert answer is not None and answer.disposition == "answer" and answer.value == "true"
