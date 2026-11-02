"""TikTok (2026-10-28): every graduate posting showed "From 2026-07-01 to 2026-12-31, you can submit up to 2 job
applications (including referrals) within the "Bachelor/Master Graduate - 2027 Start" recruitment project. You've
reached the application limit." over a full form whose Submit is refused. Three runs parked on it for the owner; a
reached application cap closes the job with the employer's own words."""

from collections import Counter
from types import SimpleNamespace

import pytest

from jobpilot.adapters.base import APPLICATION_LIMIT, Adapter, Step

NOTICE = ("From 2026-07-01 to 2026-12-31, you can submit up to 2 job applications (including referrals) within the "
          "“Bachelor/Master Graduate - 2027 Start” recruitment project. You've reached the application limit. "
          "View application history")


def field(key, label, kind="text"):
    return {"id": key, "label": label, "type": kind, "automation_id": "", "required": True, "value": "",
            "options": [], "maxlength": -1}


def form(text):
    return {"url": "https://careers.example.test/resume/1/apply", "text": text, "headings": ["Backend Engineer"],
            "fields": [field("n", "Name"), field("e", "Email"), field("r", "Resume", "file")],
            "controls": [{"id": "s", "label": "Submit", "href": "", "automation_id": "", "aria_label": ""}],
            "errors": [], "dialogs": []}


def adapter(committed=False):
    value = Adapter.__new__(Adapter)
    value.committed, value.attempts = committed, Counter()
    value.e = SimpleNamespace(form=SimpleNamespace(frames={}, page=SimpleNamespace(url="")))
    return value


async def test_a_reached_application_cap_closes_the_job_with_its_notice():
    obs = form("Application Information\n" + NOTICE + "\nReferral code\nResume\nName\nEmail\nSubmit")
    assert adapter().classify_page(obs) == Step.CLOSED
    result = await adapter().on_closed(obs)
    assert result["state"] == "closed"
    assert "application limit" in result["reason"] and "up to 2 job applications" in result["reason"]


def test_after_the_submit_the_cap_is_not_a_closure():
    assert adapter(committed=True).classify_page(form(NOTICE)) != Step.CLOSED


@pytest.mark.parametrize("text, capped", [
    ("You have reached the maximum number of applications for this period.", True),
    ("Application limit reached: you may apply again next month.", True),
    ("You can submit up to 2 job applications within this project.", False),
    ("There is no limit to the number of applications you may submit.", False),
])
def test_cap_wording(text, capped):
    assert bool(APPLICATION_LIMIT.search(text)) is capped


PROSE = ("Candidates can apply to a maximum of two positions and will be considered in the order you apply. If you have "
         "reached the application limit, this position has been filled or the job posting has closed, we will tell you; "
         "keep this page for your records and write to us for any question about your application status. " * 2)


def test_a_notice_inside_a_paragraph_of_prose_closes_nothing():
    # A job description or help paragraph may quote the wording; only the page's own notice closes (headings, error
    # banner, dialog, or a short line of its own).
    obs = form("Job description\n" + PROSE + "\nSubmit")
    assert adapter().classify_page(obs) != Step.CLOSED
    bare = {**obs, "fields": [], "controls": [], "text": "Job description\n" + PROSE}
    assert adapter().classify_page(bare) != Step.CLOSED


@pytest.mark.parametrize("place", ["errors", "headings"])
def test_a_notice_in_an_error_banner_or_heading_closes(place):
    obs = {**form("Application Information\nName\nEmail\nSubmit"), place: [NOTICE]}
    assert adapter().classify_page(obs) == Step.CLOSED


def test_the_closed_check_reads_the_page_notice_not_the_body():
    from jobpilot.adapters.base import closed_notice

    assert closed_notice({"headings": ["This position has been filled"], "text": ""})
    assert closed_notice({"errors": [], "text": "Sorry, this position has been filled.\nSearch jobs"})
    assert closed_notice({"headings": ["Engineer"], "text": PROSE}) is None


def test_a_refused_submission_that_names_limits_on_how_often_and_how_many_roles_is_the_employers_limit():
    from jobpilot.employer_limit import application_cap

    page = "Example Co has set specific limits on how often and how many roles you can apply for."
    assert application_cap(page, refused=True) == {"limit": None, "days": None}
    assert application_cap(page) is None  # A posting's own policy note holds nobody.
