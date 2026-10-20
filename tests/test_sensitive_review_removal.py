"""An SSN/ID question that reaches the review path still removes the job (Maersk, 2026-10-13)."""

from jobpilot.db import Job, Run, Setting


def make_run(service, identity):
    with service.db.session() as s:
        job = Job(
            identity=identity,
            url=f"https://acme.wd1.myworkdayjobs.com/job/{identity}",
            company="Acme",
            title="Analyst",
            status="running",
            ats="workday",
        )
        s.add(job)
        s.flush()
        run = Run(job_id=job.id, state="running", mode="submit", packet={"profile": {"autonomous": True}})
        s.add(run)
        s.flush()
        return run.id, job.id


def test_review_asking_for_ssn_cancels_and_archives(service):
    run_id, job_id = make_run(service, "ssn-1")
    service.complete(
        run_id,
        {
            "state": "needs_review",
            "reason": "Required profile information is missing",
            "reviews": [{"question": "Social Security Number", "key": "ssn"}],
        },
        1.0,
    )
    with service.db.session() as s:
        assert s.get(Run, run_id).state == "cancelled"
        assert s.get(Job, job_id).status == "archived"
        assert s.get(Setting, "job_control:" + job_id).value["state"] == "deleted"


def test_ordinary_missing_fact_still_goes_to_review(service):
    run_id, job_id = make_run(service, "plain-1")
    service.complete(
        run_id,
        {
            "state": "needs_review",
            "reason": "Required profile information is missing",
            "reviews": [{"question": "What is your expected salary?", "key": "salary"}],
        },
        1.0,
    )
    with service.db.session() as s:
        assert s.get(Run, run_id).state != "cancelled"
        assert s.get(Job, job_id).status != "archived"
