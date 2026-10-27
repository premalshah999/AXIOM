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
        Profile(first_name="Alex", last_name="Doe", accept_all_application_terms=True),
        service.db,
        service.config,
    )
    a = r.local(field(NDA))
    assert a.disposition == "answer" and a.value.startswith("Candidate Name: Alex Doe\nDate: ")


def test_no_policy_or_ordinary_question_is_not_signed(service):
    r = Resolver(Profile(first_name="Alex", last_name="Doe"), service.db, service.config)
    assert r.signature_block(field(NDA)) is None
    r = Resolver(
        Profile(first_name="Alex", last_name="Doe", accept_all_application_terms=True),
        service.db,
        service.config,
    )
    assert r.signature_block(field("Why do you want to work here?")) is None


def test_name_from_verified_facts_signs(service):
    """Live profile: first/last names are verified facts, the explicit fields are empty."""
    r = Resolver(
        Profile(facts={"first_name": "Alex", "last_name": "Doe"}, accept_all_application_terms=True),
        service.db,
        service.config,
    )
    assert r.local(field(NDA)).value.startswith("Candidate Name: Alex Doe")


# Credence (Workable) 2026-10-27: the model typed "I agree" into this box and validation refused it.
INITIAL_BOX = (
    "I have disclosed all information that is relevant and should be considered applicable to my candidacy for "
    "employment. I understand, where permissible under applicable state and local law, I may be subject to a "
    "pre-employment drug test after receiving a conditional offer of employment. I understand I may be subject to a "
    "pre-employment background check after receiving a conditional offer of employment to investigate my criminal "
    "background and other matters related to my suitability for employment. Initial to agree."
)


def test_initials_box_is_initialled(service):
    r = Resolver(
        Profile(facts={"first_name": "Alex", "last_name": "Doe"}, accept_all_application_terms=True),
        service.db,
        service.config,
    )
    a = r.local(field(INITIAL_BOX, "text"))
    assert a.disposition == "answer" and a.value == "AD"
    assert r.signature_ok(field(INITIAL_BOX, "text"), a)
    a.value = "I agree"
    assert not r.signature_ok(field(INITIAL_BOX, "text"), a)


def test_initials_need_the_policy_and_never_cover_a_disclosure(service):
    assert Resolver(Profile(first_name="Alex", last_name="Doe"), service.db, service.config).initials_block(
        field(INITIAL_BOX, "text")
    ) is None
    r = Resolver(
        Profile(first_name="Alex", last_name="Doe", accept_all_application_terms=True), service.db, service.config
    )
    assert r.initials_block(field("Have you ever been convicted of a felony? Initial to confirm.", "text")) is None
    assert r.initials_block(field("Are you related to any employee of this company? Initials:", "text")) is None
    assert r.initials_block(field("Describe your initial approach to a new codebase.", "text")) is None
