"""23 jobs had no employer and 7 no role (production, 2026-11-02), all from pasted links or short links and none on the board (a row
without both is left off it). The page read at adding names the role through its structured data but only some pages' employer:
a page whose employer is in schema.org microdata or an ATS's page props kept none. A blank employer is read from the page like a blank
role, once a day, and only what the page states about its one posting is taken (posting_identity)."""

from gallery.site import Site
from jobpilot.db import Job
from jobpilot.discovery import add_job, read_added_posting, read_blank_titles
from jobpilot.schemas import JobInput

MICRODATA = """<!doctype html><html><head><title>Job Details</title></head><body>
<div itemscope itemtype="https://schema.org/JobPosting"><meta itemprop="hiringOrganization" content="Example Corp">
<h1><span itemprop="title">Data Analyst</span></h1>
<p>Example Corp builds reporting tools. You will analyse operational data with SQL and Python and explain results to colleagues.</p></div>
</body></html>"""
NAMELESS = "<!doctype html><title>Careers at Somebody</title><h1>Data Analyst</h1><p>" + "You will analyse operational data with SQL. " * 10 + "</p>"


def make(service, site, path, **fields):
    row, _ = add_job(service.db, JobInput(url=f"{site.base}{path}", **fields), origin="manual")
    return row["id"]


async def test_a_page_that_states_its_employer_names_a_job_saved_with_a_role_and_no_employer(service):
    site = Site({"/jobs/1": MICRODATA})
    try:
        job_id = make(service, site, "/jobs/1", title="Data Analyst")
        assert await read_blank_titles(service.db, service.config) == 1
        with service.db.session() as s:
            assert s.get(Job, job_id).company == "Example Corp"
    finally:
        site.close()


async def test_a_page_that_states_no_employer_leaves_it_blank_and_is_asked_once_a_day(service):
    site = Site({"/jobs/1": NAMELESS})
    try:
        job_id = make(service, site, "/jobs/1", title="Data Analyst")
        assert await read_blank_titles(service.db, service.config) == 1
        with service.db.session() as s:
            assert s.get(Job, job_id).company == ""  # the page's <title> is free text, not the employer
        assert await read_blank_titles(service.db, service.config) == 0
    finally:
        site.close()


async def test_an_employer_a_person_or_a_listing_gave_is_never_replaced(service):
    site = Site({"/jobs/1": MICRODATA})
    try:
        job_id = make(service, site, "/jobs/1", title="Data Analyst", company="Given Name Inc")
        assert await read_added_posting(service.db, service.config, job_id)
        with service.db.session() as s:
            assert s.get(Job, job_id).company == "Given Name Inc"
    finally:
        site.close()
