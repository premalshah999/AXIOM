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
