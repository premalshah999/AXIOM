"""iCIMS names a question's free-text companion "<question>_Text" (ICE 2026-10-19: rcf3169_Text "If applicable, please
provide info." under the legal-restrictions question, required). The question's label gives it context."""

from jobpilot.forms import link_companion_text

QUESTION = "Do you have any legal restrictions to working in the country where this position is located?"


def test_a_companion_box_carries_its_question():
    fields = [
        {"name": "rcf3169_Text", "type": "text", "label": "If applicable, please provide info."},
        {"name": "rcf3169", "type": "select", "label": QUESTION},
        {"name": "rcf3048_Text", "type": "text", "label": "Other"},
        {"name": "rcf9999", "type": "text", "label": "Unrelated"},
    ]
    link_companion_text(fields)
    assert fields[0]["label"] == QUESTION + " If applicable, please provide info."
    assert fields[2]["label"] == "Other"  # no rcf3048 on this page: left alone
    link_companion_text(fields)
    assert fields[0]["label"].count(QUESTION) == 1  # idempotent across scans


def test_icims_login_box_is_the_applicants_email(service):
    from jobpilot.answers import Resolver
    from jobpilot.schemas import Profile

    resolver = Resolver(Profile(email="alex@example.test"), service.db, service.config, job={"ats": "icims", "url": "https://careers-acme.icims.com/jobs/1/x/job"})
    field = {"id": "login", "label": "Login", "type": "text", "required": True, "value": "", "valid": True, "options": []}
    answer = resolver.local(field)
    assert answer and answer.value == "alex@example.test"
    username = resolver.local({**field, "id": "u", "label": "Username"})
    assert username is None or username.value != "alex@example.test" or username.disposition != "answer"
