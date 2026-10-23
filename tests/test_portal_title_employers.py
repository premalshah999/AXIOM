"""iCIMS portals call themselves "Job Listings at PepsiCo"; 669 jobs carried that as the employer (2026-10-23), which
no receipt email names and no employer cap groups with the employer's other jobs."""

from jobpilot.db import Job, Source
from jobpilot.schemas import JobInput, employer_from_title


def test_portal_titles_give_the_employers_name():
    assert employer_from_title("Job Listings at PepsiCo") == "PepsiCo"
    assert employer_from_title("Careers at Ropes & Gray") == "Ropes & Gray"
    assert employer_from_title("Jobs At Acme") == "Acme"
    assert employer_from_title("Acme Careers") == "Acme Careers"  # not a portal-title shape
    assert employer_from_title("Job Listings at ") == "Job Listings at "  # nothing left: keep what was given
    assert JobInput(url="https://careers-acme.icims.com/jobs/1/x/job", company="Job Listings at Acme").company == "Acme"


def test_stored_jobs_and_sources_are_renamed_at_start(service):
    with service.db.exclusive() as s:
        s.add(Job(id="t1", identity="t1", url="https://careers-acme.icims.com/jobs/1/x/job",
                  company="Job Listings at Acme Corp", title="Data Engineer"))
        s.add(Job(id="t2", identity="t2", url="https://boards.example.test/2", company="Careers Inc", title="x"))
        s.add(Source(id="s1", name="Job Listings at Acme Corp", kind="career_page", url="https://careers-acme.icims.com/jobs",
                     cron="0 * * * *"))
    service.employer_names_from_titles()
    with service.db.session() as s:
        assert s.get(Job, "t1").company == "Acme Corp"
        assert s.get(Job, "t2").company == "Careers Inc"
        assert s.get(Source, "s1").name == "Acme Corp"
