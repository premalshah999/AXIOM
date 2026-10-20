"""A suburb's applicant: "closest metropolitan region" questions, and Region pickers that do not offer the applicant's
city (Taleo "Place of Residence"), are answered with the saved metro region (facts.metro_region)."""

from jobpilot.answers import Resolver
from jobpilot.schemas import Profile

MARYLAND = ["Not Specified", "Aberdeen", "Annapolis", "Gaithersburg", "Rockville", "Towson"]


def resolver(service, metro="Rockville"):
    profile = Profile(
        address={"city": "Springfield", "state": "MD", "country": "United States"},
        facts={"metro_region": metro} if metro else {},
    )
    return Resolver(profile, service.db, service.config)


def test_a_region_picker_without_the_city_takes_the_metro_region(service):
    answer = resolver(service).local({"id": "r", "label": "Region", "type": "select", "options": MARYLAND})
    assert answer.value == "Rockville" and answer.evidence_ids == ["fact:metro_region"]


def test_closest_metropolitan_region_takes_the_metro_region(service):
    r = resolver(service)
    picked = r.local({"id": "m", "label": "Closest major metropolitan region", "type": "select",
                      "options": ["Baltimore", "Rockville", "Washington, DC"]})
    assert picked.value == "Rockville"
    typed = r.local({"id": "t", "label": "Nearest metro area", "type": "text", "options": []})
    assert typed.value == "Rockville"


def test_a_picker_that_offers_the_city_is_left_to_the_city(service):
    answer = resolver(service).local({"id": "r", "label": "Region", "type": "select", "options": MARYLAND + ["Springfield"]})
    assert not answer or answer.value != "Rockville"


def test_without_a_saved_metro_region_the_city_answers_metro_questions(service):
    answer = resolver(service, metro="").local(
        {"id": "m", "label": "Closest major metropolitan region", "type": "select", "options": ["Springfield", "Baltimore"]}
    )
    assert answer.value == "Springfield"
