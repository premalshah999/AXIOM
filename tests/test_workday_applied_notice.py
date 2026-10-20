"""HPE (Workday) 2026-10-15: the posting page of a submitted application says "You applied for this job
on <date>" with a View Application button; that is already applied, not an unrecognized page."""

from jobpilot.adapters.base import ALREADY, APPLY


def test_workday_applied_notice_is_already_applied():
    assert ALREADY.search(
        "System / Software engineer II You applied for this job on October 15, 2026. View Application"
    )
    assert not APPLY.search("View Application")


def test_sign_in_footer_is_not_an_application():
    assert not ALREADY.search("Already applied? Sign in to view your application status")
