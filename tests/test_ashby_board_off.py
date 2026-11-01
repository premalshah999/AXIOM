"""An employer that turned off its Ashby board (Cursor, 11 runs on 2026-11-01): the board API lists the posting, its page and its /application page answer 404 "Page not
found", and the employer's own site shows the same form through ?embed=js. Through the real engine and a real browser, with those pages served at Ashby's address."""

import asyncio
import hashlib
from io import BytesIO

import pytest
from pypdf import PdfWriter

from jobpilot.browser import ashby_embed_url
from jobpilot.db import Job, Resume, Run
from jobpilot.discovery import add_job
from jobpilot.schemas import JobInput

POSTING = "https://jobs.ashbyhq.com/examplesoft/aef8f0e7-37cd-4d6e-96c9-3af72f588bae/application"
NOT_FOUND = "<!doctype html><title>Jobs</title><h1>Page not found</h1><p>The page you requested was not found</p><footer>Powered by Ashby</footer>"
GONE = "<!doctype html><title>Jobs</title><h1>Job not found</h1><p>The job you requested was not found.</p><a href='/examplesoft'>View all open positions</a>"
FORM = """<!doctype html><title>Software Engineer @ ExampleSoft</title><h1>Software Engineer</h1><form method="post" action="/examplesoft/submit" enctype="multipart/form-data">
<label for="n">Name</label><input id="n" name="name" required autocomplete="name"><label for="e">Email</label><input id="e" name="email" type="email" required>
<label for="r">Resume</label><input id="r" name="resume" type="file" required accept=".pdf"><button type="submit">Submit Application</button></form>"""
THANKS = "<!doctype html><title>Thanks</title><h1>Thank you for applying!</h1><p>Your application has been received. We will be in touch.</p>"


def test_only_a_posting_address_has_an_embedded_form():
    assert ashby_embed_url(POSTING) == POSTING + "?embed=js"
    assert ashby_embed_url(POSTING.removesuffix("/application")) == POSTING + "?embed=js"
    assert ashby_embed_url(POSTING + "?embed=js") == ""
    assert ashby_embed_url("https://jobs.ashbyhq.com/examplesoft") == ""
    assert ashby_embed_url("https://example.test/examplesoft/aef8f0e7-37cd-4d6e-96c9-3af72f588bae") == ""


async def run_at_ashby(service, embedded):
    config = service.config
    config.repair_agent_enabled = False
    config.mimo_api_key = "gallery-no-model"
    config.mimo_base_url = "http://127.0.0.1:9/v1"
    out = BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    writer.write(out)
    pdf = out.getvalue()
    (config.data_dir / "resumes").mkdir(exist_ok=True)
    (config.data_dir / "resumes" / "alex.pdf").write_bytes(pdf)
    with service.db.exclusive() as s:
        resume = Resume(name="Alex", filename="alex.pdf", sha256=hashlib.sha256(pdf).hexdigest(), text="Alex Example, software engineer.")
        s.add(resume)
        s.flush()
        resume_id = resume.id
    service.db.set_setting("profile", {"name": "Alex Example", "email": "alex@example.test", "autonomous": True, "facts": {"first_name": "Alex", "last_name": "Example"}})
    service.db.set_setting("control", {"paused": False, "auto_submit": True})
    posts, hits = [], []

    async def router(route):
        request = route.request
        if not request.url.startswith("https://jobs.ashbyhq.com/"):
            return await route.fallback()
        hits.append(request.url)
        if request.method == "POST":
            posts.append(request.url)
            return await route.fulfill(status=200, content_type="text/html", body=THANKS)
        if "embed=js" in request.url:
            return await route.fulfill(status=200, content_type="text/html", body=embedded)
        await route.fulfill(status=404, content_type="text/html", body=NOT_FOUND)

    async def dispatch(_run_id):
        pass

    service.fixture_router, service.dispatch = router, dispatch
    job, _ = add_job(service.db, JobInput(url=POSTING, title="Software Engineer", company="ExampleSoft", location="Remote", description="A synthetic posting."))
    with service.db.exclusive() as s:
        s.get(Job, job["id"]).status = "ready"
    run = await service.queue(job["id"], "submit", resume_id, owner_pick=True)
    await asyncio.wait_for(service.execute(run["id"]), 150)
    with service.db.session() as s:
        row = s.get(Run, run["id"])
        return row.state, row.reason, posts, hits


@pytest.mark.browser
async def test_a_board_turned_off_applies_through_the_form_its_site_embeds(service):
    state, reason, posts, hits = await run_at_ashby(service, FORM)
    assert state == "confirmed", (reason, hits)
    assert len(posts) == 1 and any("embed=js" in hit for hit in hits)


@pytest.mark.browser
async def test_a_posting_that_is_really_gone_closes_at_once(service):
    state, reason, posts, hits = await run_at_ashby(service, GONE)
    assert state == "closed", (reason, hits)
    assert not posts
