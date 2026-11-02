"""Replicas of real company career sites, through the real engine with no model: does the flow reach a form and fill it?"""

import pytest

from gallery.custom_sites import CUSTOM_SITES
from test_gallery import explain, run_scenario

pytestmark = [pytest.mark.browser, pytest.mark.gallery, pytest.mark.integration]


@pytest.mark.parametrize("scenario", CUSTOM_SITES, ids=lambda s: s.name)
async def test_the_flow_reaches_a_form_and_fills_it(service, scenario):
    site, outcome, events = await run_scenario(service, scenario)
    why = explain(scenario, site, outcome, events, service.config.data_dir)
    assert outcome["state"] == scenario.state, why
    received = site.applications()
    assert len(received) == scenario.applications, why
    if received:
        for name, value in scenario.expect.items():
            assert received[-1].get(name) == value, f"{name}: {received[-1].get(name)!r}\n{why}"
    assert outcome["model_calls"] == 0, why
