"""Bullhorn career site (2026-10-31): after its form went through, the posting's Apply button read "Already Applied". Four
later runs saw "no Apply button" and parked the job for the owner instead of recognising the employer's own status."""

import pytest

from jobpilot.adapters.base import ALREADY


def test_a_status_line_of_its_own_is_the_employer_saying_so():
    assert ALREADY.search("Share This Post\nAlready Applied\nBack\nAgentic AI Engineer")


def test_the_sign_in_footer_and_prose_are_not():
    assert not ALREADY.search("Returning candidate? Already applied? Sign in to check your status")
    assert not ALREADY.search("Already applied?\nSign in")
    assert not ALREADY.search("Candidates who have already applied do not need to reapply")


@pytest.mark.parametrize(
    "text",
    [
        "Existing applicants: If you have previously applied for a position with us, please use the same email address.",
        "If you have already applied, sign in to check your status.",
        "if you've already applied to a job here, use that account",
        "Our records show you have already applied to another position.",
        "You have previously applied for a position with us.",
    ],
)
def test_an_instruction_or_another_position_is_not_this_jobs_notice(text):
    assert not ALREADY.search(text)


@pytest.mark.parametrize(
    "text",
    [
        "You have already applied to this job.",
        "You have already applied for Machine Learning Engineer (R-71006)",
        "You've already applied for this job.",
        "You have previously applied for this position.",
    ],
)
def test_a_real_notice_still_reads_as_already_applied(text):
    assert ALREADY.search(text)
