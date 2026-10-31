"""Jobs saved without their employer or title (2026-10-31).

Pasted links, short links and crawls left 266 jobs titled "Untitled role", with no company, or with the career site's
hostname as the company ("ats.rippling.com", "careers.capgemini.com"). A posting states both in structured data; those
fill only a blank or hostname value, never a real one, and nothing is guessed from headings or page titles."""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from jobpilot.browser import BrowserEngine
from jobpilot.db import Job, Run
from jobpilot.posting_identity import (
    backfill,
    identity,
    page_inputs,
    placeholder_company,
    placeholder_title,
    save,
)


def ld(**posting):
    return json.dumps({"@context": "https://schema.org", "@type": "JobPosting", **posting})


def page(*blocks, next_data=None):
    body = "".join(f'<script type="application/ld+json">{b}</script>' for b in blocks)
    if next_data:
        body += f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(next_data)}</script>'
    return f"<html><head><title>Job Details | Somebody Else</title>{body}</head><body><h1>Careers</h1></body></html>"


def test_one_json_ld_posting_states_its_title_and_employer():
    inputs = page_inputs(page(ld(title="Data Engineer", hiringOrganization={"@type": "Organization", "name": "Acme Corp"})))
    assert identity(inputs) == {"title": "Data Engineer", "company": "Acme Corp"}


def test_a_posting_inside_a_graph_or_with_a_plain_string_employer_counts():
    graph = json.dumps({"@graph": [{"@type": "WebPage"}, {"@type": ["JobPosting"], "title": "Analyst", "hiringOrganization": "Acme"}]})
    assert identity(page_inputs(page(graph))) == {"title": "Analyst", "company": "Acme"}


def test_a_page_listing_several_postings_states_none():
    two = [ld(title="A", hiringOrganization={"name": "X"}), ld(title="B", hiringOrganization={"name": "X"})]
    assert identity(page_inputs(page(*two))) == {}


SF_PAGE = """<div itemscope itemtype="http://schema.org/JobPosting"><meta itemprop="datePosted" content="x">
<meta itemprop="hiringOrganization" content="Acme Corp"><h1><span itemprop="title" data-careersite-propertyid="title">
Data Engineer
</span></h1></div>"""


def test_successfactors_states_its_job_in_schema_org_microdata():
    # careers.capgemini.com and 111 other SuccessFactors jobs carried their site's hostname as the company.
    assert identity(page_inputs(SF_PAGE)) == {"title": "Data Engineer", "company": "Acme Corp"}
    # Two postings in the markup, or two different employers, state nothing.
    assert identity(page_inputs(SF_PAGE + SF_PAGE.replace("Acme Corp", "Other"))) == {}
    assert identity(page_inputs(SF_PAGE.replace("JobPosting", "Thing"))) == {}


def test_the_titles_and_headings_of_a_page_are_never_read():
    assert identity(page_inputs("<title>Software Engineer | Capgemini</title><h1>Software Engineer</h1>")) == {}


def test_rippling_states_its_job_in_its_page_props():
    props = {"props": {"pageProps": {"apiData": {"jobPost": {"name": "Analyst, AI", "companyName": "DESRI"}}}}}
    assert identity(page_inputs(page(next_data=props))) == {"title": "Analyst, AI", "company": "DESRI"}


def test_values_are_cleaned_and_markup_is_refused():
    inputs = page_inputs(page(ld(title="Requisition Title  Lead &amp; Staff", hiringOrganization={"name": "Careers at  Acme"})))
    assert identity(inputs) == {"title": "Lead & Staff", "company": "Acme"}
    assert identity(page_inputs(page(ld(title="<b>Engineer</b>", hiringOrganization={"name": "Acme"})))) == {"company": "Acme"}


@pytest.mark.parametrize(
    "company, url, placeholder",
    [
        ("", "https://ats.rippling.com/x/jobs/1", True),
        ("ats.rippling.com", "https://ats.rippling.com/x/jobs/1", True),
        ("careers-msci.icims.com", "https://uscareers-msci.icims.com/jobs/1/x/job", True),
        ("www.jobs.example.test", "https://jobs.example.test/job/1", True),
        # A real employer whose name looks like a hostname is not the site's host.
        ("incident.io", "https://jobs.ashbyhq.com/incident/1", False),
        ("Acme Corp", "https://jobs.example.test/job/1", False),
    ],
)
def test_a_company_that_is_just_the_sites_hostname_is_a_placeholder(company, url, placeholder):
    assert placeholder_company(company, url) is placeholder


def test_untitled_and_blank_titles_are_placeholders():
    assert placeholder_title("") and placeholder_title(None) and placeholder_title("Untitled role")
    assert not placeholder_title("Data Engineer")


def seed(service, job_id="j1", company="", title="", url="https://ats.rippling.com/acme/jobs/1", demo=False):
    with service.db.session() as s:
        s.add(Job(id=job_id, identity=job_id, url=url, company=company, title=title, ats="rippling", demo=demo))


def row(service, job_id="j1"):
    with service.db.session() as s:
        j = s.get(Job, job_id)
        return j.company, j.title


def test_save_fills_blanks_and_hostnames_and_never_overwrites_a_real_value(service):
    seed(service, "blank")
    seed(service, "host", company="ats.rippling.com", title="Untitled role")
    seed(service, "real", company="Acme Corp", title="Data Engineer")
    stated = {"company": "Other Name", "title": "Other Title"}
    assert save(service.db, "blank", stated) == stated
    assert save(service.db, "host", stated) == stated
    assert save(service.db, "real", stated) == {}
    assert row(service, "blank") == ("Other Name", "Other Title") == row(service, "host")
    assert row(service, "real") == ("Acme Corp", "Data Engineer")


def test_a_stated_company_that_is_the_sites_own_hostname_fills_nothing(service):
    seed(service, company="")
    assert save(service.db, "j1", {"company": "ats.rippling.com"}) == {}
    assert row(service) == ("", "")


def test_save_leaves_demo_jobs_and_missing_rows_alone(service):
    seed(service, "demo", demo=True)
    assert save(service.db, "demo", {"company": "Acme"}) == {} and save(service.db, "missing", {"company": "Acme"}) == {}


def engine(service, **job):
    e = BrowserEngine.__new__(BrowserEngine)
    e.db, e.emitted = service.db, []
    e.job = {"id": "j1", "url": "https://ats.rippling.com/acme/jobs/1", "company": "", "title": "", **job}
    e.emit = lambda kind, message, data=None: e.emitted.append((kind, data))
    return e


async def test_a_run_saves_what_the_open_posting_states(service):
    seed(service)
    e = engine(service)
    inputs = {"ld": [ld(title="Data Engineer", hiringOrganization={"name": "Acme Corp"})], "post": None}
    page_ = SimpleNamespace(evaluate=AsyncMock(return_value=inputs))
    await e.remember_posting_identity(page_)
    assert row(service) == ("Acme Corp", "Data Engineer")
    assert e.job["company"] == "Acme Corp" and e.job["title"] == "Data Engineer"  # receipts match on this run's copy too
    assert ("job_identity", {"company": "Acme Corp", "title": "Data Engineer"}) in e.emitted


async def test_a_job_that_has_both_never_reads_the_page(service):
    seed(service, company="Acme Corp", title="Data Engineer")
    e = engine(service, company="Acme Corp", title="Data Engineer")
    page_ = SimpleNamespace(evaluate=AsyncMock())
    await e.remember_posting_identity(page_)
    page_.evaluate.assert_not_awaited()


async def test_a_page_that_cannot_be_read_costs_the_run_nothing(service):
    seed(service)
    e = engine(service)
    await e.remember_posting_identity(SimpleNamespace(evaluate=AsyncMock(side_effect=RuntimeError("navigation"))))
    assert row(service) == ("", "") and e.emitted == []


def test_backfill_reports_first_and_writes_only_when_told(service, tmp_path):
    seed(service, "j1")  # a placeholder with a saved posting page
    seed(service, "j2", company="ats.rippling.com", title="Data Analyst")  # a hostname, no run
    seed(service, "j3", company="Acme Corp", title="Data Engineer")  # nothing to fill
    seed(service, "j4")  # a placeholder whose saved page states nothing
    with service.db.session() as s:
        s.add(Run(id="r1", job_id="j1", state="confirmed", mode="submit", packet={}))
        s.add(Run(id="r4", job_id="j4", state="closed", mode="submit", packet={}))
    for run_id, html in (("r1", page(ld(title="Data Engineer", hiringOrganization={"name": "Acme Corp"}))), ("r4", "<h1>Hi</h1>")):
        steps = Path(tmp_path) / run_id / "steps"
        steps.mkdir(parents=True)
        (steps / "01-form.html").write_text(html)
    report = backfill(service.db, tmp_path)
    assert (report["placeholders"], report["with_runs"], report["with_saved_pages"], report["fixable"]) == (3, 2, 2, 1)
    assert report["company"] == report["title"] == 1 and report["examples"] == [
        {"job": "j1", "company": "Acme Corp", "title": "Data Engineer"}
    ]
    assert row(service) == ("", "")  # a report writes nothing
    backfill(service.db, tmp_path, apply=True)
    assert row(service) == ("Acme Corp", "Data Engineer") and row(service, "j3") == ("Acme Corp", "Data Engineer")
    assert row(service, "j2") == ("ats.rippling.com", "Data Analyst") and row(service, "j4") == ("", "")


async def test_the_live_page_is_read_the_same_way_as_a_saved_one(page):
    from jobpilot.posting_identity import IDENTITY_JS

    for markup, expected in (
        (SF_PAGE, {"title": "Data Engineer", "company": "Acme Corp"}),
        (page_with_ld(), {"title": "Data Engineer", "company": "Acme Corp"}),
        ("<title>Data Engineer | Acme</title><h1>Data Engineer</h1>", {}),
    ):
        await page.set_content(markup)
        assert identity(await page.evaluate(IDENTITY_JS)) == expected
        assert identity(page_inputs(markup)) == expected


def page_with_ld():
    return page(ld(title="Data Engineer", hiringOrganization={"name": "Acme Corp"}))
