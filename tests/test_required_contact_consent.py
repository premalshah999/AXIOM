"""CVS Health 2026-10-14: a required text-communication consent follows the terms policy."""

from jobpilot.answers import Resolver
from jobpilot.schemas import Profile

LABEL = "I authorize CVS Health to communicate with me via text (standard message rates may apply)"


def ask(required=True, accept=True, options=("Yes", "No"), kind="select"):
    r = Resolver(Profile(accept_all_application_terms=accept), None, None)
    return r.required_contact_consent(
        {
            "id": "c",
            "label": LABEL,
            "type": kind,
            "required": required,
            "options": list(options),
            "value": "",
            "valid": True,
        }
    )


def test_required_contact_consent_is_accepted():
    a = ask()
    assert a.value == "Yes" and a.evidence_ids == ["fact:application_consent_policy"]
    assert ask(kind="checkbox", options=()).value == "true"


def test_optional_or_without_policy_is_not_decided_here():
    assert ask(required=False) is None
    assert ask(accept=False) is None
    assert ask(options=("Maybe later", "Not now")) is None
