"""AbbVie 2026-10-14: an optional 'Select all' must not opt into optional SMS/WhatsApp marketing."""

from jobpilot.answers import Resolver
from jobpilot.schemas import Profile


def field(label, required):
    return {
        "id": "q",
        "label": label,
        "type": "checkbox",
        "required": required,
        "options": [],
        "value": "",
        "valid": True,
    }


def test_optional_select_all_stays_unchecked(service):
    r = Resolver(Profile(accept_all_application_terms=True), service.db, service.config)
    a = r.local(field("Select all - by selecting all, you acknowledge the checkboxes below", False))
    assert a.value == "false" and a.disposition == "answer"


def test_required_privacy_notice_is_still_accepted(service):
    r = Resolver(Profile(accept_all_application_terms=True), service.db, service.config)
    a = r.local(
        field(
            "You declare that you have read and agree to the job applications privacy notice of AbbVie.", True
        )
    )
    assert a is not None and a.value == "true"
