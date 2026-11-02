"""A posting open in several countries is listed for its US places, and the row names only those: "Austin, TX; Toronto, Canada" read as
a foreign row (production, 2026-11-02: 23 listings name Canada, India or the UK among US places)."""

import pytest

from jobpilot.jobboard import us_places


@pytest.mark.parametrize("location,shown", [
    ("Bellevue, WA; Mountain View, CA; Vancouver, Canada", "Bellevue, WA; Mountain View, CA"),
    ("Remote, Canada; Remote, United States", "Remote, United States"),
    ("London, UK; SF; Austin, TX; Remote in USA; Remote in Canada", "SF; Austin, TX; Remote in USA"),
    ("Dallas / Toronto", "Dallas"),
    ("Remote in India; Austin, TX; Santa Clara, CA", "Austin, TX; Santa Clara, CA"),
])
def test_only_the_places_not_named_as_another_country_are_shown(location, shown):
    assert us_places(location) == shown


@pytest.mark.parametrize("location", [
    "Dublin, OH", "New Mexico", "Austin, TX", "Santa Clara, CA; Boston, MA", "US-CA-Menlo Park", "Toronto, Canada", "", "Remote",
])
def test_a_single_place_or_one_with_nothing_foreign_is_left_as_it_was(location):
    assert us_places(location) == location
