"""CVS's "New Mexico; Washington; ..." read as Mexico and AIG's Dublin as Ireland (2026-10-23): US jobs were refused as
foreign. A list of locations qualifies when one of them is in the US."""

import pytest

from jobpilot.jobboard import us_location


@pytest.mark.parametrize("location,us", [
    ("Albuquerque, New Mexico", True), ("New Mexico; Washington; Kansas", True), ("Mexico City, Mexico", False),
    ("Los Angeles, CA; Toronto, Canada", True), ("Dublin, OH", True), ("Dublin, Ireland", False),
    ("London, UK; Paris, France", False), ("Toronto, Ontario, Canada", False), ("Bengaluru, KA, IN", False),
    ("Remote - US", True),
])
def test_us_locations(location, us):
    assert us_location(location) is us


@pytest.mark.parametrize("title,german", [
    ("AI Backend Developer - m/w/d", True), ("PEGA Developer - w/m/d", True), ("Data Engineer (m/f/d)", True),
    ("Data Engineer", False), ("B2B/SaaS Analyst", False), ("Engineer, M/W/F schedule", False),
])
def test_german_role_marker(title, german):
    # Sopra Steria's "Bundesweit, DE" (Germany) read as Delaware (2026-10-27); the title marks the German posting.
    from jobpilot.jobboard import GERMAN_ROLE

    assert bool(GERMAN_ROLE.search(title)) is german
