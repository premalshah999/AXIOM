"""Owner 2026-10-28: "remove all the stupid allowances ... no blockages till I tell you". Open repairs never hold new
applications, and there is no new-application allowance to run out."""

from jobpilot.autopilot import new_applications_held, open_repairs
from test_owner_questions_20261023 import add_job


def test_a_repairing_application_never_holds_new_ones(service):
    add_job(service, "stuck", state="waiting_browser")
    from jobpilot.db import Run

    with service.db.exclusive() as s:
        s.get(Run, "run-stuck").state = "waiting_browser"
    assert open_repairs(service.db) == 1
    assert new_applications_held(service.db) == 0
