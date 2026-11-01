"""A posting with the site's own search boxes and an Apply button is the job page, not the application form (2026-10-25..31)."""

from collections import Counter
from unittest.mock import MagicMock

import pytest

from jobpilot.adapters.base import Adapter, Step, site_filter


def adapter():
    value = Adapter.__new__(Adapter)
    value.attempts = Counter()
    value.apply_ids = set()
    value.committed = False
    value.link_resume = False
    value.in_assessment = lambda: False
    value.e = MagicMock()
    value.e.form.frames = {}
    return value


def field(label, kind="text", required=False, **extra):
    return {"id": label, "label": label, "type": kind, "required": required, "options": [], **extra}


def control(label):
    return {"id": label, "label": label, "aria_label": "", "automation_id": "", "href": ""}


def page(fields, controls=("Apply Now",)):
    return {"fields": fields, "controls": [control(c) for c in controls], "text": "", "headings": [], "errors": [], "dialogs": [], "url": ""}


@pytest.mark.parametrize(
    "labels",
    [
        ["Search for jobs by title or keyword", "Search job by location", "English"],
        ["I am looking for", "Where"],
        ["Find Jobs"],
        ["Country", "Job Category", "Keywords", "Studio/Department"],
        ["Search example.com"],
        ["Skip to main content"],
    ],
)
def test_search_and_filter_boxes_beside_apply_are_the_job_page(labels):
    assert adapter().classify_page(page([field(x) for x in labels])) == Step.JOB


@pytest.mark.parametrize(
    "fields",
    [
        [field("First Name"), field("Email", "email")],
        [field("Search for jobs"), field("First Name")],
        [field("Country", required=True)],
        [field("Why do you want to work here?", "textarea")],
        [field("How did you hear about us?")],
    ],
)
def test_a_real_form_with_an_apply_button_stays_a_form(fields):
    assert adapter().classify_page(page(fields)) == Step.FORM


def test_search_boxes_with_no_apply_button_decide_nothing_new():
    assert adapter().classify_page(page([field("Find Jobs")], controls=("Home",))) == Step.FORM


def test_the_scan_marking_a_search_box_counts():
    assert site_filter(field("Tagline", search_box=True))
    assert not site_filter(field("Tagline", search_box=True, required=True))


@pytest.mark.parametrize(
    "fields",
    [
        [field("Resume", "file")],
        [field("Search by Location"), field("Write a reply...", "textarea"), field("Upload Video", "file")],
        [field("Write a reply...", "textarea"), field("Upload Video", "file")],
    ],
)
def test_a_posting_widget_beside_apply_is_the_job_page(fields):
    """Eightfold's "match your resume" file input and SuccessFactors' comment box and video upload (2026-11-01) read as
    the form on 10 live postings that each showed an Apply button."""
    assert adapter().classify_page(page(fields)) == Step.JOB


@pytest.mark.parametrize(
    "fields",
    [
        [field("Resume", "file", required=True)],
        [field("Resume", "file"), field("First Name")],
        [field("Write a reply...", "textarea"), field("Upload Video", "file"), field("Phone", "tel")],
    ],
)
def test_a_required_file_or_another_question_still_makes_it_the_form(fields):
    assert adapter().classify_page(page(fields)) == Step.FORM
