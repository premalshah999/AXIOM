"""Owner rule (2026-10-17): the city is Springfield, but "closest metropolitan region" questions, and Region pickers that
do not offer Springfield (Taleo Bealls "Place of Residence"), are answered with the saved metro region, Bethesda."""

from jobpilot.answers import Resolver
from jobpilot.schemas import Profile

MARYLAND = ["Not Specified", "Aberdeen", "Annapolis", "Bethesda", "Gaithersburg", "Rockville"]


def resolver(service, metro="Bethesda"):
    profile = Profile(
        address={"city": "Springfield", "state": "MD", "country": "United States"},
        facts={"metro_region": metro} if metro else {},
    )
    return Resolver(profile, service.db, service.config)


def test_a_region_picker_without_the_city_takes_the_metro_region(service):
    answer = resolver(service).local({"id": "r", "label": "Region", "type": "select", "options": MARYLAND})
    assert answer.value == "Bethesda" and answer.evidence_ids == ["fact:metro_region"]


def test_closest_metropolitan_region_takes_the_metro_region(service):
    r = resolver(service)
    picked = r.local({"id": "m", "label": "Closest major metropolitan region", "type": "select",
                      "options": ["Baltimore", "Bethesda", "Washington, DC"]})
    assert picked.value == "Bethesda"
    typed = r.local({"id": "t", "label": "Nearest metro area", "type": "text", "options": []})
    assert typed.value == "Bethesda"


def test_a_picker_that_offers_the_city_is_left_to_the_city(service):
    answer = resolver(service).local({"id": "r", "label": "Region", "type": "select", "options": MARYLAND + ["Springfield"]})
    assert not answer or answer.value != "Bethesda"


def test_without_a_saved_metro_region_the_city_answers_metro_questions(service):
    answer = resolver(service, metro="").local(
        {"id": "m", "label": "Closest major metropolitan region", "type": "select", "options": ["Springfield", "Baltimore"]}
    )
    assert answer.value == "Springfield"
