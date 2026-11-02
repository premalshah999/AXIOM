"""Every trap a person found by eye, driven through the real engine in a real browser, to a submit the local site received.

The 4,300 tests of the gate never open a browser. The browser tests do, but nearly all of them call one helper on one page made with
set_content, with the engine built by hand around it (`Adapter.__new__`), so nothing asks whether the run as a whole does the right
thing: the bugs the owner found were in the seams between those helpers. Here nothing is mocked between the queue and the employer:
`Service.queue` and `Service.execute`, `BrowserEngine.run`, its own headless Chrome, the navigator, the form code, the commit. The
assertions are the employer's record of what arrived and the state the run ended in. No model and no real site is involved: a model
call fails fast against a closed local port, and a run that made one fails its test.
"""

import asyncio
import hashlib

import pytest
from pypdf import PdfWriter
from sqlalchemy import select

from jobpilot.db import Event, Job, Resume, Run
from jobpilot.company_sites import allow
from jobpilot.discovery import add_job
from jobpilot.schemas import JobInput

from gallery.scenarios import SCENARIOS
from gallery.site import Site

pytestmark = [pytest.mark.browser, pytest.mark.gallery, pytest.mark.integration]
RUN_SECONDS = 150  # Generous on a busy Mac: a healthy scenario takes 3 to 20 s.


def blank_pdf():
    from io import BytesIO

    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    out = BytesIO()
    writer.write(out)
    return out.getvalue()


async def run_scenario(service, scenario, mode="submit"):
    """The site, a job at its address, one run through the real engine. Returns (site, run row values, events)."""
    config = service.config
    config.repair_agent_enabled = False  # It would ask a model about a failed run: a test's failure stays what it is.
    config.mimo_api_key = "gallery-no-model"
    config.mimo_base_url = "http://127.0.0.1:9/v1"  # A closed port: a model call cannot reach anything.
    pdf = blank_pdf()
    (config.data_dir / "resumes").mkdir(exist_ok=True)
    (config.data_dir / "resumes" / "alex.pdf").write_bytes(pdf)
    with service.db.exclusive() as s:
        resume = Resume(name="Alex", filename="alex.pdf", sha256=hashlib.sha256(pdf).hexdigest(), text="Alex Example, software engineer.")
        s.add(resume)
        s.flush()
        resume_id = resume.id
    service.db.set_setting("profile", {"name": "Alex Example", "email": "alex@example.test", "autonomous": True,
                                       "accept_all_application_terms": scenario.terms,
                                       "facts": {"first_name": "Alex", "last_name": "Example", **scenario.facts}, **scenario.profile})
    service.db.set_setting("control", {"paused": False, "auto_submit": True})

    async def dispatch(_run_id):
        pass

    service.dispatch = dispatch
    site = Site(scenario.pages)
    job, _ = add_job(service.db, JobInput(url=site.base + scenario.start, title=scenario.title, company=scenario.company,
                                          location="Remote", description="A synthetic posting."))
    with service.db.exclusive() as s:
        s.get(Job, job["id"]).status = "ready"  # What ranking leaves a job in.
    allow(service.db, [job["id"]])  # A local page reads as a company's own site: the owner's click sends it.
    try:
        run = await service.queue(job["id"], mode, resume_id, owner_pick=True)
        await asyncio.wait_for(service.execute(run["id"]), RUN_SECONDS)
    finally:
        site.close()
    with service.db.session() as s:
        row = s.get(Run, run["id"])
        outcome = {"id": row.id, "state": row.state, "reason": row.reason, "model_calls": row.model_calls, "receipt": row.receipt}
        events = [(e.kind, e.message) for e in s.scalars(select(Event).where(Event.run_id == run["id"]).order_by(Event.id))]
    return site, outcome, events


def explain(scenario, site, outcome, events, data_dir):
    trail = "\n".join(f"  {kind}: {message[:110]}" for kind, message in events[-25:])
    return (f"{scenario.name} ended {outcome['state']}: {outcome['reason'][:200]}\nThe employer received {site.posts}\n"
            f"Asked for {site.hits}\nFinal screenshot: {data_dir}/runs/{outcome['id']}/final.png\n{trail}")


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s.name)
async def test_the_engine_gets_through_the_trap(service, scenario):
    site, outcome, events = await run_scenario(service, scenario)
    why = explain(scenario, site, outcome, events, service.config.data_dir)
    assert outcome["state"] == scenario.state, why
    received = site.applications()
    assert len(received) == scenario.applications, why
    if received:
        for name, value in scenario.expect.items():
            assert received[-1].get(name) == value, f"{name}: {received[-1].get(name)!r}\n{why}"
        if "resume" in received[-1]:
            assert received[-1].get("resume__bytes"), "The resume arrived empty\n" + why
        for name in scenario.uploaded:
            assert received[-1].get(name + "__bytes"), f"{name} arrived empty\n{why}"
    for path in scenario.never:
        assert path not in site.hits, f"{path} must never be touched\n{why}"
    assert outcome["model_calls"] == 0, why


async def test_a_dry_run_reaches_the_final_step_and_sends_nothing(service):
    scenario = SCENARIOS[0]
    site, outcome, events = await run_scenario(service, scenario, mode="dry_run")
    assert outcome["state"] == "dry_run_passed", explain(scenario, site, outcome, events, service.config.data_dir)
    assert site.posts == []
