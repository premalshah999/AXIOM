"""One career site's application page through the real engine, to a submit the local site received (see gallery/career_profile.py)."""

import pytest

from gallery.career_profile import SCENARIOS as PROFILE_PAGES
from gallery.platforms import SCENARIOS as PLATFORM_PAGES
from jobpilot.answers.contact import address_location_option
from jobpilot.browser import is_dismiss_control
from test_gallery import explain, run_scenario

CITY = type("A", (), {"city": "Exampleton", "state": "MD", "country": "United States of America", "county": ""})()


@pytest.mark.browser
@pytest.mark.gallery
@pytest.mark.integration
@pytest.mark.parametrize("scenario", [*PROFILE_PAGES, *PLATFORM_PAGES], ids=lambda s: s.name)
async def test_the_engine_submits_this_page(service, scenario):
    site, outcome, events = await run_scenario(service, scenario)
    why = explain(scenario, site, outcome, events, service.config.data_dir)
    assert outcome["state"] == scenario.state, why
    received = site.applications()
    assert len(received) == scenario.applications, why
    for name, value in scenario.expect.items():
        assert received[-1].get(name) == value, f"{name}: {received[-1].get(name)!r}\n{why}"
    assert received[-1].get("resume__bytes"), why
    if scenario.name.startswith("a_create_account"):
        assert any(where == "/account/details" for where, _ in site.posts), why
    for path in scenario.never:
        assert path not in site.hits, f"{path} must never be touched\n{why}"
    assert outcome["model_calls"] == 0, why


def test_a_city_list_of_city_and_state_rows_finds_the_applicants_own():
    rows = ["Exampleton Hills, IL", "Exampleton, TN", "Exampleton, MD", "Exampleton, QLD, Australia"]
    assert address_location_option(rows, CITY) == "Exampleton, MD"
    assert address_location_option(["Exampleton, TN", "Other, MD"], CITY) is None
    assert address_location_option(["Exampleton, Maryland, United States"], CITY) == "Exampleton, Maryland, United States"


@pytest.mark.parametrize("label", ["Close", "Close dialog", "Close Sign up for Career Profile dialog", "Cancel"])
def test_a_close_button_that_names_its_dialog_closes_it(label):
    assert is_dismiss_control({"label": label})


@pytest.mark.parametrize("label", ["Sign up", "Close my account", "Close account dialogue", "Submit"])
def test_other_controls_do_not_close_a_dialog(label):
    assert not is_dismiss_control({"label": label})
