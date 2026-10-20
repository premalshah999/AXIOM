"""Astreya 2026-10-14: an NDA text box ending "Candidate Name: Date:" is signed under the terms policy."""

from jobpilot.answers import Resolver
from jobpilot.schemas import Profile

NDA = (
    "Non-Disclosure/Confidentiality Agreement ... the Candidate hereby agrees and acknowledges ... I hereby certify "
    "that the answers given by me are true. In the answer, please include these items: Candidate Name: Date:"
)


def field(label, type_="textarea"):
    return {
        "id": "q",
        "label": label,
        "type": type_,
        "required": True,
        "options": [],
        "value": "",
        "valid": True,
    }


def test_nda_signature_block_is_signed(service):
    r = Resolver(
        Profile(first_name="Alex", last_name="Shah", accept_all_application_terms=True),
        service.db,
        service.config,
    )
    a = r.local(field(NDA))
    assert a.disposition == "answer" and a.value.startswith("Candidate Name: Alex Doe\nDate: ")


def test_no_policy_or_ordinary_question_is_not_signed(service):
    r = Resolver(Profile(first_name="Alex", last_name="Shah"), service.db, service.config)
    assert r.signature_block(field(NDA)) is None
    r = Resolver(
        Profile(first_name="Alex", last_name="Shah", accept_all_application_terms=True),
        service.db,
        service.config,
    )
    assert r.signature_block(field("Why do you want to work here?")) is None


def test_name_from_verified_facts_signs(service):
    """Live profile: first/last names are verified facts, the explicit fields are empty."""
    r = Resolver(
        Profile(facts={"first_name": "Alex", "last_name": "Shah"}, accept_all_application_terms=True),
        service.db,
        service.config,
    )
    assert r.local(field(NDA)).value.startswith("Candidate Name: Alex Doe")
