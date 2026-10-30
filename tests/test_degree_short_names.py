"""Short degree names (BE, BS, BTech, BEng, MS, MSc ...) choose the tenant's real option by level once its options are
read. Humana's Workday Degree (2026-10-30) offered "Bachelor's Degree": Education 2's "BE" committed it; Education 1's
Degree never had its options read (a popup covered the button), so this mapping was not the fault, but "BEng" was the one
engineering short name the lookup lacked while "MEng" had it."""

import pytest

from jobpilot.answers import Resolver
from jobpilot.schemas import Profile
from jobpilot.widgets import best_option

CATALOGS = {
    "possessive": ["Associate's Degree", "Bachelor's Degree", "Master's Degree", "Doctorate"],
    "plural": ["Associates", "Bachelors", "Masters", "Doctorate"],
    "degree word": ["Associates Degree", "Bachelors Degree", "Masters Degree", "Doctoral Degree"],
}
LEVEL = {"possessive": ("Bachelor's Degree", "Master's Degree"), "plural": ("Bachelors", "Masters"),
         "degree word": ("Bachelors Degree", "Masters Degree")}
BACHELOR = ["BE", "B.E.", "BS", "B.S.", "BSc", "BTech", "B.Tech", "BEng", "B.Eng.", "Bachelor of Engineering"]
MASTER = ["MS", "M.S.", "MSc", "M.Sc.", "MEng", "M.Eng.", "MTech", "M.Tech", "Master of Science"]


@pytest.mark.parametrize("catalog", CATALOGS)
@pytest.mark.parametrize("recorded", BACHELOR + MASTER)
def test_a_short_degree_name_maps_to_the_offered_level(catalog, recorded):
    bachelor, master = LEVEL[catalog]
    assert best_option(CATALOGS[catalog], recorded) == (bachelor if recorded in BACHELOR else master)


@pytest.mark.parametrize("recorded", BACHELOR + MASTER)
def test_the_owner_policy_picks_the_same_level_when_the_catalog_has_no_literal_match(service, recorded):
    profile = Profile(first_name="Alex", education=[{"school": "Example University", "degree": recorded,
                                                     "field_of_study": "Computer Science", "end": "2022-05"}])
    resolver = Resolver(profile, service.db, service.config, job={"ats": "workday", "url": "https://example.test"})
    field = {"id": "d", "label": "Degree", "type": "dropdown", "group": "Education 1", "section": "", "required": True}
    # A catalog of named degrees: the level's own degree is the offered equivalent, never the other level's.
    named = ["Bachelor of Science", "Master of Science", "Doctor of Philosophy"]
    assert resolver.policy_choice(field, named) == ("Bachelor of Science" if recorded in BACHELOR else "Master of Science")
