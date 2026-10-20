"""Seniority titles that slipped through on 10-17: GE Vernova "... AI & Data Science Leader" and Citi "Agentic AI
Engineer (AVP)" were queued for a new-grad profile. "Leadership Development Program" (a new-grad rotation) stays."""

import pytest

from jobpilot.features.discovery.policy import SENIOR as DISCOVERY_SENIOR
from jobpilot.jobboard import SENIOR as BOARD_SENIOR


@pytest.mark.parametrize("pattern", [BOARD_SENIOR, DISCOVERY_SENIOR])
def test_leader_and_avp_titles_are_senior(pattern):
    assert pattern.search("Nuclear Engineering AI & Data Science Leader")
    assert pattern.search("Agentic AI Engineer (AVP)")
    assert pattern.search("Team Lead, Machine Learning")
    assert not pattern.search("Technology Leadership Development Program - Software Engineer")
    assert not pattern.search("Software Engineer, New Grad")
