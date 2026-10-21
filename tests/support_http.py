"""Rows for the HTTP route tests. Everything is synthetic: example.test hosts, invented titles and companies."""

from jobpilot.db import Job, Run


def seed_job(service, n=1, *, status="new", ats="greenhouse", demo=False):
    """Insert a job directly (no ranking, no network) and return its id."""
    with service.db.exclusive() as s:
        job = Job(
            identity=f"example.test/jobs/{n}",
            url=f"https://boards.example.test/acme/jobs/{n}",
            title=f"Platform Engineer {n}",
            company="Acme Example",
            ats=ats,
            status=status,
            demo=demo,
        )
        s.add(job)
        s.flush()
        return job.id


def seed_run(service, job_id, *, state="queued", mode="submit", receipt=None, reason=""):
    with service.db.exclusive() as s:
        run = Run(job_id=job_id, state=state, mode=mode, receipt=receipt or {}, reason=reason)
        s.add(run)
        s.flush()
        return run.id
