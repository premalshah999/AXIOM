"""Owner reconciliation of a run that started its commit and was parked (Dayforce, 2026-10-16)."""

import pytest

from jobpilot.db import Event, Job, Run


def seed(service, state, committed):
    with service.db.exclusive() as s:
        job = Job(identity=f"rc-{state}-{committed}", url=f"https://example.test/{state}{committed}", company="Acme", title="Engineer")
        s.add(job)
        s.flush()
        run = Run(job_id=job.id, state=state, mode="submit", packet={})
        s.add(run)
        s.flush()
        if committed:
            s.add(Event(run_id=run.id, kind="submitting", message="Commit step started"))
        return run.id, job.id


def test_a_committed_parked_run_can_be_confirmed_by_the_owner(service):
    run_id, job_id = seed(service, "needs_review", True)
    service.reconcile(run_id, True, "Employer page: Congratulations, your application has been submitted!")
    with service.db.session() as s:
        assert s.get(Run, run_id).state == "confirmed" and s.get(Job, job_id).status == "confirmed"


def test_a_run_that_never_committed_cannot_be_reconciled(service):
    run_id, _ = seed(service, "waiting_browser", False)
    with pytest.raises(ValueError):
        service.reconcile(run_id, True, "claimed")


def test_the_owner_can_record_a_parked_application_they_submitted_themselves(service):
    # Schwab 2026-10-21: the run parked on its profile page; the owner finished it in the browser.
    run_id, job_id = seed(service, "waiting_browser", False)
    service.reconcile(run_id, True, "Your application was submitted successfully.", submitted_by_owner=True)
    with service.db.session() as s:
        run = s.get(Run, run_id)
        assert run.state == "confirmed" and run.receipt["type"] == "owner_submitted"
        assert s.get(Job, job_id).status == "confirmed"


def test_an_active_run_cannot_be_recorded_as_owner_submitted(service):
    run_id, _ = seed(service, "running", False)
    with pytest.raises(ValueError):
        service.reconcile(run_id, True, "claimed", submitted_by_owner=True)


def test_an_unsent_form_goes_to_repair_not_to_an_empty_needs_you(service):
    # Five jobs sat in "Needs you" with no question after the repair agent read their final page as unsent (2026-10-29).
    run_id, job_id = seed(service, "needs_review", True)
    service.reconcile(run_id, False, "Repair agent read the final page: the unsent form is still shown")
    with service.db.session() as s:
        run = s.get(Run, run_id)
        assert run.state == "waiting_browser" and run.receipt["type"] == "unsent_reconciled"
        assert run.reason.startswith("Not submitted")
        assert s.get(Job, job_id).status == "waiting_browser"
