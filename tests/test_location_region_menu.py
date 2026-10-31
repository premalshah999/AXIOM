"""A closed location menu of regions rather than cities takes the region holding the applicant's address."""

import pytest

from jobpilot.answers import Resolver
from jobpilot.answers.contact import address_region_option
from jobpilot.schemas import Profile

MENU = ["New York", "USA (other than New York)", "Europe", "Other"]


def address(city, state, country="United States"):
    return Profile(address={"city": city, "state": state, "country": country}).address


@pytest.mark.parametrize(
    "where,expected",
    [
        (address("Springfield", "Illinois"), "USA (other than New York)"),
        (address("Albany", "New York"), "New York"),
        (address("New York", "New York"), "New York"),
        (address("Paris", "Ile-de-France", "France"), None),
        (address("Springfield", "Illinois", ""), None),
    ],
)
def test_the_menu_takes_the_region_holding_the_address(where, expected):
    assert address_region_option(MENU, where) == expected


def test_two_regions_holding_the_address_decide_nothing():
    assert address_region_option(["USA", "United States (excluding Alaska)"], address("Springfield", "Illinois")) is None


def test_the_country_excluding_the_applicants_state_is_not_theirs():
    assert address_region_option(["USA (other than Illinois)", "Illinois"], address("Springfield", "Illinois")) == "Illinois"
    assert address_region_option(["USA (excluding Illinois)", "Europe"], address("Springfield", "Illinois")) is None


def test_the_resolver_answers_a_location_menu_from_the_address(service):
    profile = Profile(location="Springfield, IL", address={"city": "Springfield", "state": "Illinois", "country": "United States"})
    resolver = Resolver(profile, service.db, service.config)
    answer = resolver.local(
        {"id": "q", "label": "Location", "type": "combobox", "required": True, "options": MENU, "value": "", "valid": True}
    )
    assert answer.value == "USA (other than New York)"
    assert answer.evidence_ids == ["profile:address.city", "profile:address.state", "profile:address.country"]
