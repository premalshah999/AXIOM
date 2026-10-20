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
