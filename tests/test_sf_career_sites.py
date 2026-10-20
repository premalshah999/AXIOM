"""SuccessFactors Career Site Builder on employer domains (2026-10-15): careers.ey.com, jobs.cargill.com
and similar hosts were saved as custom career sites, so neither the SuccessFactors handling nor the
owner's ATS focus applied to their jobs."""

from sqlalchemy import select

from jobpilot.ats import canonicalize, detect
from jobpilot.db import Job


def test_career_site_builder_hosts_are_successfactors():
    for url in (
        "https://careers.ey.com/ey/job/Dallas-SDC-Back-End-Engineer-TX-75270/1442523233/",
        "https://jobs.cargill.com/job/Atlanta-Cloud-Platform-Engineer-Geor-30340/1373146857/",
        "https://careers.capgemini.com/job/Dallas-MLOps-Engineer-TX/1234567/",
    ):
        assert detect(url).id == "successfactors"
    assert detect("https://www.ey.com/en_us/careers").id == "custom"


def test_saved_custom_jobs_take_the_recognized_ats(service):
    url = "https://careers.capgemini.com/job/Dallas-MLOps-Engineer-TX/1234567/"
    with service.db.session() as s:
        s.add(Job(url=url, identity=canonicalize(url)[1], ats="custom", title="MLOps Engineer"))
        s.add(Job(url="https://example.org/jobs/1", identity="example:1", ats="custom", title="Analyst"))
    service.relabel_custom_ats()
    with service.db.session() as s:
        labels = {job.url: job.ats for job in s.scalars(select(Job))}
    assert labels[url] == "successfactors"
    assert labels["https://example.org/jobs/1"] == "custom"
