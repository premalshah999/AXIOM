"""The repair agent reads evidence with the applicant's email masked as "[email]". It traced "[email]" to the masked
answer_used and saved it as the Email answer, and every later run typed "[email]" into the box: "The format of the email
address is not valid" (iPipeline 2026-10-19; seven saved records held the placeholder)."""

from types import SimpleNamespace

from jobpilot.answers import Resolver
from jobpilot.repair_agent import check_fixes
from jobpilot.schemas import Profile


def test_a_masked_override_never_answers(service):
    service.db.set_setting("repair_overrides:job-1", {"email": {"label": "Email", "value": "[email]", "run_id": "r"}})
    resolver = Resolver(Profile(email="alex@example.test"), service.db, service.config, job={"id": "job-1", "ats": "icims", "url": "https://careers-acme.icims.com/jobs/1/x/job"})
    answer = resolver.local({"id": "e", "label": "Email", "type": "email", "required": True, "value": "", "valid": True, "options": []})
    assert answer and answer.value == "alex@example.test"


def test_the_repair_agent_refuses_a_masked_fix():
    plan = SimpleNamespace(field_fixes=[SimpleNamespace(label="Email", value="[email]")])
    ev = {"page": {"fields": [{"label": "Email", "answer_used": "[email]", "options": []}]}}
    accepted, rejected = check_fixes(plan, ev, Profile(email="alex@example.test"))
    assert not accepted and rejected == [("Email", "a masked placeholder is not an answer")]


def test_a_masked_phone_number_is_not_an_answer_either():
    """mask() writes the applicant's number as nines with the format kept; the agent traced "9999999999" to the
    masked answer_used and saved it as the Phone override (Stripe 2026-10-18)."""
    for value in ["9999999999", "(999) 999-9999", "+9 999 999 9999", "999.999.9999", "Call 9999999999"]:
        plan = SimpleNamespace(field_fixes=[SimpleNamespace(label="Phone", value=value)])
        ev = {"page": {"fields": [{"label": "Phone", "answer_used": value, "options": []}]}}
        accepted, rejected = check_fixes(plan, ev, Profile(email="alex@example.test"))
        assert not accepted and rejected == [("Phone", "a masked placeholder is not an answer")], value


def test_real_numbers_are_not_mistaken_for_the_mask():
    from jobpilot.repair_agent import MASKED_VALUE

    for value in ["12345", "3.9", "2024", "(555) 555-0100", "99999", "1999999", "9 years", "May 99"]:
        assert not MASKED_VALUE.search(value), value


def test_a_saved_masked_phone_override_never_answers(service):
    service.db.set_setting("repair_overrides:job-2", {"phone": {"label": "Phone", "value": "9999999999", "run_id": "r"}})
    resolver = Resolver(
        Profile(email="alex@example.test", phone="5555550100"),
        service.db,
        service.config,
        job={"id": "job-2", "ats": "greenhouse", "url": "https://job-boards.greenhouse.io/acme/jobs/1"},
    )
    answer = resolver.local({"id": "p", "label": "Phone", "type": "tel", "required": True, "value": "", "valid": True, "options": []})
    assert not answer or answer.value != "9999999999"
