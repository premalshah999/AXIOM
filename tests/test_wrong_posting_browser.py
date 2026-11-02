"""The 2026-11-01 incident, replayed through the real engine on the desktop Chrome path, with a model set on doing what that run's model did.

A saved route is reopened and bounces to the career home; the model opens the list, another role and its Apply; the receipt names that role.
Each guard is proven alone by turning off the ones before it, and with all three off the replica reproduces the incident, so a pass here
means the guards are what stopped it. Then the closest legitimate cases. Names are invented.
"""

import asyncio
import json
import os
import shutil
import tempfile
from urllib.parse import urljoin

import pytest

from gallery.scenarios import NAME_FIELDS, EMAIL_FIELD, POSTING, RESUME_FIELD, SUBMIT_BUTTON, THREE_PAGES, form, page
from gallery.site import Scenario
from jobpilot import browser as browser_module
from jobpilot.browser import BrowserEngine, NavigationDecision
from test_gallery import explain, run_scenario

pytestmark = [pytest.mark.browser, pytest.mark.gallery, pytest.mark.integration]

OWN = "/careers/JobDetail/New-York-Software-Engineer/13045"
ROUTE = "/careers/ApplicationFormWidget?jobId=13045"
OTHER = "/careers/JobDetail/New-York-Data-Analyst/14102"


def career_home(_request=None):
    return page(
        "Find Jobs",
        """<form action="/careers/OpenRoles" method="get" role="search"><label for="k">Keywords</label><input id="k" name="k">
        <label for="w">Location</label><input id="w" name="w"><button type="submit">Search</button></form>
        <p><a href="/careers/OpenRoles">See All Openings</a></p><p><a href="/careers/about">About Us</a></p><h2>Roles</h2>""" + "".join(
            f'<p><a href="{href}">{label}</a></p>'
            for href, label in (
                (OTHER, "Data Analyst"),
                ("/careers/JobDetail/Tokyo-Kubernetes-Tech-Lead/14350", "Kubernetes Tech Lead"),
                ("/careers/JobDetail/London-Credit-Risk-Associate/14377", "Credit Risk Associate"),
            )
        ),
    )


def application_form(request):
    """The form of whichever posting its address names, as the site's own form does."""
    job_id = (request.query.get("jobId") or [""])[0]
    hidden = f'<input type="hidden" name="jobId" value="{job_id}">'
    return page("Apply", "<h1>Application</h1>" + form(hidden, NAME_FIELDS, EMAIL_FIELD, RESUME_FIELD, SUBMIT_BUTTON))


def receipt(request):
    role = {"13045": "Software Engineer", "14102": "Data Analyst"}.get(request.form.get("jobId"), "this position")
    return f"""<!doctype html><html lang="en"><meta charset="utf-8"><title>Application Successful</title><body><h1>Thank you</h1>
    <p>Your application for {role} has been submitted.</p></body></html>"""


PAGES = {
    # A route without its session bounces to the career home, which is what started the incident.
    "/careers/ApplicationFormWidget": lambda request: (302, "/careers"),
    "/careers": career_home,
    "/careers/OpenRoles": career_home,
    OWN: page("Software Engineer", POSTING + '<p><a class="apply" href="/careers/ApplicationForm?jobId=13045">Apply</a></p>'),
    OTHER: page("Data Analyst", '<h1>Data Analyst</h1><p><a class="apply" href="/careers/ApplicationForm?jobId=14102">Apply</a></p>'),
    "/careers/ApplicationForm": application_form,
    "/submit/1": receipt,
}
INCIDENT = Scenario("incident", "A saved route bounced to the career home.", PAGES, start=OWN)


def wandering_model(monkeypatch):
    """What the incident's model did: open the list, open a role, Apply, fill, submit. Records every control it was offered."""
    offered = []

    async def structured(_config, _db, schema, _system, payload, *_args, **_kwargs):
        assert schema is NavigationDecision
        observation = json.loads(payload)["observation"]
        labels = {c["label"]: c["id"] for c in observation["controls"]}
        offered.append(sorted(labels))
        for wanted in ("See All Openings", "Data Analyst", "Apply"):
            if wanted in labels:
                return NavigationDecision(action="click", control_id=labels[wanted], reason="wander")
        if any(f.get("required") and not f.get("filled") for f in observation["fields"]):
            return NavigationDecision(action="fill", reason="Fill the form")
        if "Submit application" in labels:
            return NavigationDecision(action="submit", reason="Send it")
        return NavigationDecision(action="help", reason="Nothing here for this job")

    monkeypatch.setattr("jobpilot.models.structured", structured)
    return offered


@pytest.fixture
async def desktop_chrome(service):
    """The engine attaches to its own Chrome as the installed app does: a saved route is only ever reopened on that path."""
    from playwright.async_api import async_playwright

    temp = tempfile.mkdtemp(prefix="wrongjob-chrome-")
    playwright = await async_playwright().start()
    executable = os.environ.get("CHROMIUM_PATH") or playwright.chromium.executable_path
    await playwright.stop()
    argv = browser_module.throwaway_chrome(executable, temp, True)  # Its own profile, and a mock keychain: no dialog on anyone's screen.
    process = await asyncio.create_subprocess_exec(
        *argv, "about:blank", stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
    )
    portfile = os.path.join(temp, "DevToolsActivePort")
    for _ in range(100):
        if os.path.exists(portfile) and os.path.getsize(portfile):
            break
        await asyncio.sleep(0.1)
    with open(portfile) as f:
        service.config.browser_cdp_url = f"http://127.0.0.1:{f.read().splitlines()[0]}"
    yield
    process.kill()
    await process.wait()
    shutil.rmtree(temp, ignore_errors=True)


def saved_route(monkeypatch):
    """A route saved by the run before. A real one holds https only, and a local site is http, so its stored form is what's replaced."""

    def state(self, _saved):
        return {"url": urljoin(self.job["url"], ROUTE)}

    monkeypatch.setattr(BrowserEngine, "desktop_resume_state", state)


def guard_one_off(monkeypatch):
    from jobpilot.posting_match import PostingWatch

    async def stays(self, _obs):
        return False

    monkeypatch.setattr(BrowserEngine, "stay_on_posting", stays)
    monkeypatch.setattr(PostingWatch, "other_posting_link", lambda *_a, **_k: False)


def guard_two_off(monkeypatch):
    async def nothing(self):
        return None

    monkeypatch.setattr(BrowserEngine, "other_posting_at_commit", nothing)


def guard_three_off(monkeypatch):
    monkeypatch.setattr("jobpilot.posting_match.receipt_conflict", lambda *_a, **_k: None)


async def replay(service):
    site, outcome, events = await run_scenario(service, INCIDENT)
    return site, outcome, events, explain(INCIDENT, site, outcome, events, service.config.data_dir)


def sent_job_ids(site):
    return [form_values.get("jobId") for _path, form_values in site.posts]


async def test_all_three_guards_the_run_goes_back_and_applies_to_its_own_job(service, monkeypatch, desktop_chrome):
    offered = wandering_model(monkeypatch)
    saved_route(monkeypatch)
    site, outcome, events, why = await replay(service)
    assert outcome["state"] == "confirmed", why
    assert sent_job_ids(site) == ["13045"], why
    assert OTHER not in site.hits, why
    assert "/careers/ApplicationFormWidget" in site.hits, why  # It did start from the saved route, and it did bounce.
    assert any(kind == "posting_guard" for kind, _ in events), why
    assert "Your application for Software Engineer" in outcome["receipt"]["confirmation"], why
    assert offered == [] or not any("Data Analyst" in labels for labels in offered), why


async def test_guard_one_alone_when_the_posting_will_not_open(service, monkeypatch, desktop_chrome):
    offered = wandering_model(monkeypatch)
    saved_route(monkeypatch)
    scenario = Scenario("gone", "", {**PAGES, OWN: lambda request: (302, "/careers")}, start=OWN)
    site, outcome, events = await run_scenario(service, scenario)
    why = explain(scenario, site, outcome, events, service.config.data_dir)
    assert outcome["state"] == "waiting_browser", why
    assert outcome["reason"].startswith("The employer's site didn't open this posting"), why
    assert site.posts == [] and OTHER not in site.hits and "/careers/ApplicationForm" not in site.hits, why
    assert offered == [], why  # The model is never shown the list.


async def test_guard_two_alone_stops_the_submit_on_the_other_posting(service, monkeypatch, desktop_chrome):
    wandering_model(monkeypatch)
    saved_route(monkeypatch)
    guard_one_off(monkeypatch)
    monkeypatch.setattr("jobpilot.adapters.base.Adapter.leave_site_page", _never_leaves, raising=False)
    site, outcome, events, why = await replay(service)
    assert "/careers/ApplicationForm" in site.hits and OTHER in site.hits, why  # It really did wander to the other posting.
    assert site.posts == [], why
    assert outcome["state"] == "waiting_browser", why
    assert outcome["reason"].startswith("Not submitted:"), why


async def test_guard_three_alone_keeps_the_receipt_from_confirming_this_job(service, monkeypatch, desktop_chrome):
    wandering_model(monkeypatch)
    saved_route(monkeypatch)
    guard_one_off(monkeypatch)
    guard_two_off(monkeypatch)
    monkeypatch.setattr("jobpilot.adapters.base.Adapter.leave_site_page", _never_leaves, raising=False)
    site, outcome, events, why = await replay(service)
    assert sent_job_ids(site) == ["14102"], why  # One application reached the other posting, once.
    assert outcome["state"] == "submission_unknown", why
    assert "Data Analyst" in outcome["reason"] and "Software Engineer" in outcome["reason"], why
    assert outcome["receipt"].get("other_posting") is True and not outcome["receipt"].get("website_confirmed"), why


async def test_with_all_three_off_the_replica_is_the_incident(service, monkeypatch, desktop_chrome):
    wandering_model(monkeypatch)
    saved_route(monkeypatch)
    guard_one_off(monkeypatch)
    guard_two_off(monkeypatch)
    guard_three_off(monkeypatch)
    monkeypatch.setattr("jobpilot.adapters.base.Adapter.leave_site_page", _never_leaves, raising=False)
    site, outcome, events, why = await replay(service)
    assert sent_job_ids(site) == ["14102"], why
    assert outcome["state"] == "confirmed", why


async def _never_leaves(self, _obs):
    return False


def model_with_labels(monkeypatch, wanted):
    """A navigator that always goes for a link named in `wanted` when it is offered, and otherwise asks for help."""
    seen = []

    async def structured(_config, _db, schema, _system, payload, *_args, **_kwargs):
        assert schema is NavigationDecision
        controls = json.loads(payload)["observation"]["controls"]
        seen.append([c["label"] for c in controls])
        for control in controls:
            if control["label"] in wanted:
                return NavigationDecision(action="click", control_id=control["id"], reason="wander")
        return NavigationDecision(action="help", reason="Nothing here for this job")

    monkeypatch.setattr("jobpilot.models.structured", structured)
    return seen


NO_APPLY = page(
    "Software Engineer",
    POSTING + f"""<h2>Similar jobs</h2><p><a href="{OTHER}">Data Analyst</a></p>
    <p><a href="/careers/roles/14350">Kubernetes Tech Lead</a></p><p><a href="/careers/roles/14377">View role</a></p>""",
)


async def test_the_model_is_not_offered_another_posting_on_the_jobs_own_page(service, monkeypatch):
    seen = model_with_labels(monkeypatch, {"Data Analyst", "Kubernetes Tech Lead", "View role"})
    scenario = Scenario("model_on_own_page", "", {**PAGES, OWN: NO_APPLY}, start=OWN, state="waiting_browser", applications=0)
    site, outcome, events = await run_scenario(service, scenario)
    why = explain(scenario, site, outcome, events, service.config.data_dir)
    assert seen, why
    assert all(not {"Data Analyst", "Kubernetes Tech Lead", "View role"} & set(labels) for labels in seen), why
    assert OTHER not in site.hits and "/careers/roles/14350" not in site.hits, why
    assert site.posts == [], why


async def test_a_button_with_no_address_to_read_is_sent_back_from_the_other_posting(service, monkeypatch):
    """A control the scan can't read an address from: the click runs, the next look finds another posting, and the run goes back. Nothing is
    ever sent, and the other posting's Apply is never put in front of the model."""
    seen = model_with_labels(monkeypatch, {"Open the data role"})
    own_page = page(
        "Software Engineer",
        POSTING + f"""<button type="button" onclick="location.href='{OTHER}'">Open the data role</button>""",
    )
    scenario = Scenario("model_clicks_button", "", {**PAGES, OWN: own_page}, start=OWN, state="waiting_browser", applications=0)
    site, outcome, events = await run_scenario(service, scenario)
    why = explain(scenario, site, outcome, events, service.config.data_dir)
    assert site.posts == [] and "/careers/ApplicationForm" not in site.hits, why
    assert outcome["state"] == "waiting_browser", why
    assert any(kind == "posting_guard" for kind, _ in events), why
    assert not any("Apply" in labels for labels in seen), why


async def test_the_apply_link_to_another_number_is_refused_and_never_followed(service, monkeypatch):
    model_with_labels(monkeypatch, set())
    scenario = Scenario(
        "apply_link_elsewhere", "",
        {"/jobs/70011": page("Software Engineer", POSTING + '<p><a class="apply" href="/jobs/70022">Apply</a></p>'),
         "/jobs/70022": page("Data Analyst", "<h1>Data Analyst</h1>"), **THREE_PAGES},
        start="/jobs/70011", state="waiting_browser", applications=0,
    )  # fmt: skip
    site, outcome, events = await run_scenario(service, scenario)
    why = explain(scenario, site, outcome, events, service.config.data_dir)
    assert "/jobs/70022" not in site.hits and site.posts == [], why


# ----- a step's button can be the employer's real submit, with nothing armed ---------------------------------------------------------

SIMILAR_ROWS = "".join(
    f'<p><a href="/jobs/{n}">{title}</a></p>'
    for n, title in ((70031, "Data Analyst"), (70032, "Kubernetes Tech Lead"), (70033, "Credit Risk Associate"))
)
STEP_THAT_SENDS = page(
    "Apply",
    "<h1>Apply: Software Engineer</h1>"
    + form(NAME_FIELDS, EMAIL_FIELD, RESUME_FIELD, '<p><button id="go" type="submit">Continue</button></p>'),
)


def sends_on_continue(words):
    return Scenario(
        "unarmed_send", "",
        {"/jobs/1": page("Software Engineer", POSTING + '<p><a class="apply" href="/apply/1">Apply for this job</a></p>'),
         "/apply/1": STEP_THAT_SENDS,
         "/submit/1": page("Thanks", f"<h1>Thanks</h1><p>{words}</p><h2>Similar jobs</h2>" + SIMILAR_ROWS)},
    )  # fmt: skip


async def test_a_receipt_with_similar_jobs_is_never_left_to_apply_again(service, monkeypatch):
    model_with_labels(monkeypatch, set())
    scenario = sends_on_continue("Thank you for applying! Your application has been received.")
    site, outcome, events = await run_scenario(service, scenario)
    why = explain(scenario, site, outcome, events, service.config.data_dir)
    assert len(site.applications()) == 1, why
    assert outcome["state"] == "confirmed", why


async def test_a_page_after_a_send_that_says_nothing_is_never_left_to_apply_again(service, monkeypatch):
    """No acknowledgement to read: what stops it is that a write that may have been the application has gone out."""
    model_with_labels(monkeypatch, set())
    scenario = sends_on_continue("We will be in touch.")
    site, outcome, events = await run_scenario(service, scenario)
    why = explain(scenario, site, outcome, events, service.config.data_dir)
    assert len(site.applications()) == 1, why
    assert site.hits.count("/apply/1") == 1, why
    assert outcome["state"] != "confirmed", why


async def test_without_these_checks_the_guard_would_have_sent_it_twice(service, monkeypatch):
    """The hazard the safety review found, reproduced: so a pass above means the checks are what stopped it."""
    from jobpilot import browser

    model_with_labels(monkeypatch, set())

    async def nothing(self):
        return None

    monkeypatch.setattr(BrowserEngine, "page_acknowledged", nothing)
    monkeypatch.setattr(browser, "acknowledges_application", lambda _text: False)
    monkeypatch.setattr("jobpilot.posting_match.acknowledges_application", lambda _text: False)
    monkeypatch.setattr(BrowserEngine, "note_possible_send", lambda *_a: None)
    scenario = sends_on_continue("Thank you for applying! Your application has been received.")
    site, outcome, events = await run_scenario(service, scenario)
    why = explain(scenario, site, outcome, events, service.config.data_dir)
    assert len(site.applications()) >= 2, why
