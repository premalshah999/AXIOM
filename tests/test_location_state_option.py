"""A location box that lists regions as "State, Country" takes the applicant's own state in the applicant's own country."""

import pytest

from jobpilot.answers.contact import address_state_option
from jobpilot.schemas import Profile

ROWS = ["Maryland, United States", "Maryland, Liberia"]


def address(state, country="United States of America", city="Exampleton"):
    return Profile(address={"city": city, "state": state, "country": country}).address


@pytest.mark.parametrize("state", ["MD", "Maryland", "md"])
def test_the_state_in_the_applicants_country_is_chosen(state):
    assert address_state_option(ROWS, address(state)) == "Maryland, United States"


def test_a_same_named_region_of_another_country_is_never_chosen():
    assert address_state_option(["Maryland, Liberia"], address("MD")) is None


def test_a_missing_state_or_country_decides_nothing():
    assert address_state_option(ROWS, address("")) is None
    assert address_state_option(ROWS, address("MD", "")) is None


def test_country_rows_and_city_rows_are_not_regions():
    assert address_state_option(["United States", "Exampleton, Maryland, United States"], address("MD")) is None
