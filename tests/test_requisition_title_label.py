"""iCIMS' "Requisition Title" screen-reader label is not part of a job title. A listing crawl took the link's text and 26
jobs were titled "Requisition Title Lead Data Scientist" (2026-10-30)."""

import pytest
from bs4 import BeautifulSoup
from sqlalchemy import select, text

from jobpilot.db import Job
from jobpilot.discovery import listing_title
from jobpilot.schemas import JobInput, job_title

ROW = (
    '<a href="https://careers.example.test/jobs/42/lead-data-scientist/job?in_iframe=1" class="iCIMS_Anchor" '
    'title="42 - Lead Data Scientist"> <span class="sr-only field-label">Requisition Title</span> '
    '<h3 > Lead Data Scientist</h3> </a>'
)


def test_a_listing_link_reads_its_title_without_the_screen_reader_label():
    assert listing_title(BeautifulSoup(ROW, "html.parser").a) == "Lead Data Scientist"
    plain = BeautifulSoup('<a href="/jobs/1/x/job">Staff Engineer</a>', "html.parser").a
    assert listing_title(plain) == "Staff Engineer"


@pytest.mark.parametrize(
    "raw,clean",
    [
        ("Requisition Title Lead Data Scientist", "Lead Data Scientist"),
        ("Requisition Title: Cloud Data Engineer", "Cloud Data Engineer"),
        ("  requisition title   C Software Developer (Emerging Careers)", "C Software Developer (Emerging Careers)"),
        ("Requisition Title", ""),
        ("Data Scientist", "Data Scientist"),
        ("Requisition Titles Analyst", "Requisition Titles Analyst"),
        ("Lead Requisition Title Analyst", "Lead Requisition Title Analyst"),
        ("", ""),
    ],
)
def test_the_label_is_stripped_from_the_front_of_a_title_only(raw, clean):
    assert job_title(raw) == clean
    assert JobInput(url="https://example.test/job/1", title=raw).title == clean


def test_stored_titles_that_carry_the_label_read_clean_everywhere(service):
    """No data write: the row keeps the label until its title is next written; every read drops it."""
    with service.db.exclusive() as s:
        s.add(Job(id="dirty", identity="dirty", url="https://example.test/job/9", status="ready"))
    with service.db.exclusive() as s:
        s.execute(text("UPDATE jobs SET title = 'Requisition Title Lead Data Scientist' WHERE id = 'dirty'"))
    with service.db.session() as s:
        assert s.get(Job, "dirty").title == "Lead Data Scientist"  # the entity
        assert s.execute(select(Job.title).where(Job.id == "dirty")).scalar_one() == "Lead Data Scientist"  # a column select
        assert s.execute(text("SELECT title FROM jobs WHERE id = 'dirty'")).scalar_one().startswith("Requisition Title")
        assert [j.id for j in s.scalars(select(Job).where(Job.title.like("Requisition Title%")))] == ["dirty"]
