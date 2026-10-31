"""Bullhorn career site (2026-10-31): after its form went through, the posting's Apply button read "Already Applied". Four
later runs saw "no Apply button" and parked the job for the owner instead of recognising the employer's own status."""

from jobpilot.adapters.base import ALREADY


def test_a_status_line_of_its_own_is_the_employer_saying_so():
    assert ALREADY.search("Share This Post\nAlready Applied\nBack\nAgentic AI Engineer")


def test_the_sign_in_footer_and_prose_are_not():
    assert not ALREADY.search("Returning candidate? Already applied? Sign in to check your status")
    assert not ALREADY.search("Already applied?\nSign in")
    assert not ALREADY.search("Candidates who have already applied do not need to reapply")
