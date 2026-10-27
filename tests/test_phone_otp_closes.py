"""Owner 2026-10-27: an application that needs a code sent to the phone is closed (Lumen's Eightfold SMS step waited
for the owner); a phone-number field error is not such a step."""

from jobpilot.db import Run
from test_owner_questions_20261023 import add_job


def test_a_phone_code_requirement_closes_the_application(service):
    run_id = add_job(service, "sms", state="running")
    with service.db.exclusive() as s:
        s.get(Run, run_id).state = "running"
    service.complete(run_id, {"state": "waiting_browser",
                              "reason": "SMS verification requires an employer session; Gmail cannot supply this code"}, 1)
    with service.db.session() as s:
        run = s.get(Run, run_id)
        assert run.state == "closed" and "phone (SMS) verification" in run.reason


def test_a_phone_number_field_error_is_not_a_phone_code(service):
    run_id = add_job(service, "phonefield", state="running")
    with service.db.exclusive() as s:
        s.get(Run, run_id).state = "running"
    service.complete(run_id, {"state": "waiting_browser",
                              "reason": "The page reports Phone Number is required although the field is filled"}, 1)
    with service.db.session() as s:
        assert s.get(Run, run_id).state == "waiting_browser"
