"""Humana (Phenom front end, Workday back end), 2026-11-01: the application was posted (POST /applySubmit, then a click that timed
out), the saved route reopened the site's "One Last Step! ... creating your Candidate Home Account" page, and the run spent its account
window signing in to Workday and loading Candidate Home, then stopped as "did not reach an application form" -- with the application
already received."""

import pytest

from jobpilot.browser import received_application_page

JOB = "https://careers.humana.com/us/en/job/HUMHUMUSR434028EXTERNALENUS/AI-Engineer?_ccid=1"
THANKS = "https://careers.humana.com/us/en/applythankyou?status=success&jobSeqNo=HUMHUMUSR434028EXTERNALENUS&jobId=R-434028"


def test_the_employers_success_page_for_this_posting_is_a_receipt():
    assert received_application_page(THANKS, JOB)


@pytest.mark.parametrize(
    "url",
    [
        "https://careers.humana.com/us/en/applythankyou?status=failure&jobSeqNo=HUMHUMUSR434028EXTERNALENUS",
        "https://careers.humana.com/us/en/applythankyou?status=success&jobSeqNo=HUMOTHERJOB999999",  # another posting's
        "https://careers.humana.com/us/en/applythankyou?status=success",  # names no posting
        "https://careers.humana.com/us/en/apply?status=success&jobSeqNo=HUMHUMUSR434028EXTERNALENUS",  # not a thank-you path
        "https://careers.humana.com/us/en/job/HUMHUMUSR434028EXTERNALENUS/AI-Engineer",
    ],
)
def test_nothing_else_is_taken_for_a_receipt(url):
    assert not received_application_page(url, JOB)
