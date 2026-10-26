"""Greenhouse redirects an accepted embed POST to /embed/job_app/confirmation?for=<board>&token=<job>. Invisible
Technologies (2026-10-23..26) draws its acknowledgement as an image, so text checks never confirmed it; the route for
this exact board and job, with no application form left, is the receipt."""

import pytest

from jobpilot.answers import Resolver
from jobpilot.forms import FormSession
from jobpilot.schemas import Profile

THANKS = """<html><body><img alt="" src="data:image/gif;base64,R0lGODlhAQABAAAAACw=">
<a href="https://job-boards.greenhouse.io/acme">View more jobs at Acme</a> <a href="#">Back to job post</a></body></html>"""
FORM = """<html><body><form><input type=file><textarea></textarea><button type=submit>Submit application</button>
</form></body></html>"""


async def proof_at(page, tmp_path, url, html, job_url):
    async def serve(route):
        await route.fulfill(status=200, content_type="text/html", body=html)

    await page.route("https://job-boards.greenhouse.io/**", serve)
    await page.goto(url)
    form = FormSession(page, Resolver(Profile(), None, None, job={"ats": "greenhouse", "url": job_url}),
                       tmp_path / "r.pdf", lambda *a, **k: None)
    return await form.proof()


@pytest.mark.parametrize("job_url", [
    "https://job-boards.greenhouse.io/embed/job_app?for=acme&token=4643870101",
    "https://job-boards.greenhouse.io/acme/jobs/4643870101",
])
async def test_the_confirmation_route_for_this_job_is_the_receipt(page, tmp_path, job_url):
    receipt = await proof_at(page, tmp_path, "https://job-boards.greenhouse.io/embed/job_app/confirmation?for=acme&token=4643870101",
                             THANKS, job_url)
    assert receipt and receipt["evidence_selector"] == "greenhouse_embed_confirmation_route"


async def test_another_jobs_route_or_a_form_is_not(page, tmp_path):
    job = "https://job-boards.greenhouse.io/acme/jobs/4643870101"
    other = await proof_at(page, tmp_path, "https://job-boards.greenhouse.io/embed/job_app/confirmation?for=acme&token=999",
                           THANKS, job)
    assert not other or other["evidence_selector"] != "greenhouse_embed_confirmation_route"
    form = await proof_at(page, tmp_path, "https://job-boards.greenhouse.io/embed/job_app/confirmation?for=acme&token=4643870101",
                          FORM, job)
    assert not form or form["evidence_selector"] != "greenhouse_embed_confirmation_route"
