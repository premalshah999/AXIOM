"""The guided browser, with a model trying to do the wrong thing, in a real browser through the real engine. Names are invented."""

import json

import pytest

from gallery.scenarios import OTHER_ROLES, POSTING, THREE_PAGES, page
from gallery.site import Scenario
from test_gallery import explain, run_scenario

pytestmark = [pytest.mark.browser, pytest.mark.gallery, pytest.mark.integration]


def eager_model(monkeypatch, labels):
    """A navigator that always wants the link named in `labels` when it is offered, and otherwise asks for help. Records what it saw."""
    from jobpilot.browser import NavigationDecision

    seen = []

    async def structured(_config, _db, schema, _system, payload, *_args, **_kwargs):
        assert schema is NavigationDecision
        controls = json.loads(payload)["observation"]["controls"]
        seen.append([c["label"] for c in controls])
        for control in controls:
            if control["label"] in labels:
                return NavigationDecision(action="click", control_id=control["id"], reason="wander")
        return NavigationDecision(action="help", reason="Nothing here for this job")

    monkeypatch.setattr("jobpilot.models.structured", structured)
    return seen


NO_APPLY = page(
    "Software Engineer",
    POSTING
    + """<h2>Similar jobs</h2><p><a href="/careers/JobDetail/New-York-Data-Analyst/14102">Data Analyst</a></p>
    <p><a href="/careers/JobDetail/Tokyo-Kubernetes-Tech-Lead/14350">Kubernetes Tech Lead</a></p>""",
)
OWN = "/careers/JobDetail/New-York-Software-Engineer/13045"


async def test_the_model_is_not_offered_another_posting_on_the_jobs_own_page(service, monkeypatch):
    seen = eager_model(monkeypatch, {"Data Analyst", "Kubernetes Tech Lead"})
    scenario = Scenario("model_on_own_page", "", {OWN: NO_APPLY, **OTHER_ROLES}, start=OWN, state="waiting_browser", applications=0)
    site, outcome, events = await run_scenario(service, scenario)
    why = explain(scenario, site, outcome, events, service.config.data_dir)
    assert seen, why
    assert all("Data Analyst" not in labels and "Kubernetes Tech Lead" not in labels for labels in seen), why
    assert "/careers/JobDetail/New-York-Data-Analyst/14102" not in site.hits, why
    assert site.posts == [], why


async def test_a_link_the_scan_cannot_reject_still_leaves_the_posting_only_to_be_sent_back(service, monkeypatch):
    """A control with no address to read (a button that navigates): the click runs, the next look finds another posting, and the run
    goes home; nothing is ever sent, and the model gets no second turn on the other posting's page."""
    seen = eager_model(monkeypatch, {"Open the data role"})
    own_page = page(
        "Software Engineer",
        POSTING + """<button type="button" onclick="location.href='/careers/JobDetail/New-York-Data-Analyst/14102'">Open the data role</button>""",
    )
    scenario = Scenario("model_clicks_button", "", {OWN: own_page, **OTHER_ROLES}, start=OWN, state="waiting_browser", applications=0)
    site, outcome, events = await run_scenario(service, scenario)
    why = explain(scenario, site, outcome, events, service.config.data_dir)
    assert site.posts == [] and "/careers/ApplicationForm" not in site.hits, why
    assert outcome["state"] == "waiting_browser", why
    assert any(kind == "posting_guard" for kind, _ in events), why
    assert not any("Apply" in labels for labels in seen), why  # The other posting's Apply was never put in front of the model.


async def test_the_apply_link_to_another_number_is_refused_and_never_followed(service, monkeypatch):
    eager_model(monkeypatch, set())
    scenario = Scenario(
        "apply_link_elsewhere", "",
        {"/jobs/70011": page("Software Engineer", POSTING + '<p><a class="apply" href="/jobs/70022">Apply</a></p>'),
         "/jobs/70022": page("Data Analyst", "<h1>Data Analyst</h1>"), **THREE_PAGES},
        start="/jobs/70011", state="waiting_browser", applications=0,
    )  # fmt: skip
    site, outcome, events = await run_scenario(service, scenario)
    why = explain(scenario, site, outcome, events, service.config.data_dir)
    assert "/jobs/70022" not in site.hits and site.posts == [], why
