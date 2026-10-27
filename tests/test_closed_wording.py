"""Closed-posting wording beyond "no longer available" (Paylocity, Monti 2026-10-26)."""

import pytest

from jobpilot.adapters.base import CLOSED


@pytest.mark.parametrize("text", [
    "We're sorry, that job does not exist or is not currently active.",
    "The position you are looking for no longer exists.",
    "This requisition is not currently open.",
    "We're sorry\u2026 the job you are trying to apply for has been filled. Maybe you would like to consider",
])
def test_closed_wording(text):
    assert CLOSED.search(text)


@pytest.mark.parametrize("text", [
    "Tell us about the job that does not exist yet but you would love to do.",
    "Describe a position that no longer exists at your company.",
    "Apply now",
    "Has a vacancy ever been filled through your referral?",
])
def test_not_closed(text):
    assert not CLOSED.search(text)
