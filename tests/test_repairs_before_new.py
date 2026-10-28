"""Owner 2026-10-27: no new applications while any application is repairing, unless the owner allows them."""

from jobpilot.autopilot import new_applications_held, open_repairs
from test_owner_questions_20261023 import add_job


def test_a_repairing_application_holds_new_ones_until_the_owner_allows_them(service):
    assert new_applications_held(service.db) == 0
    add_job(service, "stuck", state="waiting_browser")
    from jobpilot.db import Run

    with service.db.exclusive() as s:
        s.get(Run, "run-stuck").state = "waiting_browser"
    assert open_repairs(service.db) == 1 and new_applications_held(service.db) == 1
    service.db.set_setting("new_applications", {"allowed": True})
    assert new_applications_held(service.db) == 0


def test_a_limited_allowance_lapses_after_that_many_new_applications(service):
    from jobpilot.db import Run, now

    add_job(service, "stuck", state="waiting_browser")
    with service.db.exclusive() as s:
        s.get(Run, "run-stuck").state = "waiting_browser"
    service.db.set_setting("new_applications", {"allowed": True, "limit": 1, "since": now()})
    assert new_applications_held(service.db) == 0  # The stuck job's run predates the allowance.
    add_job(service, "fresh", state="confirmed")
    with service.db.exclusive() as s:
        s.get(Run, "run-fresh").created_at = now()
    assert new_applications_held(service.db) == 1  # One new application started: the allowance is used up.
