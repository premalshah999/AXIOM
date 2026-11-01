"""A job added from a pasted link on a plain career page kept "Role not read yet" for good: only API-listed postings were read at
that moment (clean-install run, 2026-11-01). The role is read when the job is added, and for rows saved before, once at start."""

from gallery.site import Site
from jobpilot.db import Job
from jobpilot.discovery import add_job, read_added_posting, read_blank_titles
from jobpilot.schemas import JobInput

PAGE = """<!doctype html><html><head><title>Careers at Example Corp</title></head><body><h1>Data Analyst</h1>
<p>Example Corp builds reporting tools. You will analyse operational data with SQL and Python and explain results to colleagues.</p>
<a href="/apply">Apply for this job</a></body></html>"""
LIST = "<!doctype html><title>Jobs</title><h1>Open roles</h1><h1>Another heading</h1><p>" + "A long list of roles. " * 20 + "</p>"


def make(service, site, path):
    row, _ = add_job(service.db, JobInput(url=f"{site.base}{path}"), origin="manual")
    return row["id"]


async def test_the_role_is_read_from_the_page_when_the_job_is_added(service):
    site = Site({"/jobs/1": PAGE})
    try:
        job_id = make(service, site, "/jobs/1")
        assert await read_added_posting(service.db, service.config, job_id)
        with service.db.session() as s:
            assert s.get(Job, job_id).title == "Data Analyst"
    finally:
        site.close()


async def test_jobs_saved_without_a_role_are_read_once_at_start_and_a_list_page_names_none(service):
    site = Site({"/jobs/1": PAGE, "/jobs": LIST})
    try:
        one, many = make(service, site, "/jobs/1"), make(service, site, "/jobs")
        assert await read_blank_titles(service.db, service.config) == 2
        with service.db.session() as s:
            assert s.get(Job, one).title == "Data Analyst"
            assert "Open roles" not in (s.get(Job, many).title or "")  # several headings: it is a list, not a role
        assert await read_blank_titles(service.db, service.config) == 0  # tried today
    finally:
        site.close()
