"""Oracle "My profile" application card proves a submission for exactly this job (Ford, 2026-10-13)."""

import pytest

from jobpilot.answers import Resolver
from jobpilot.forms import FormSession
from jobpilot.schemas import Profile

JOB = "https://efds.fa.em5.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/job/72609"
PROFILE = "https://efds.fa.em5.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/my-profile"


async def serve(page, url, body):
    async def route(request):
        await request.fulfill(status=200, content_type="text/html", body=body)

    await page.context.route("**/*", route)
    await page.goto(url)


def card(href, extra=""):
    return (
        f"<ul><li class='cs-application-tile'><a href='{href}'>Full Stack Software Engineer</a>"
        f"<div>Ford Global Career Site 72609</div><div>Applied on 10/10/2026</div>"
        f"<label>Status: <select><option>Under Consideration</option></select></label>{extra}</li></ul>"
    )


@pytest.mark.parametrize(
    "href,extra,proved",
    [
        (JOB, "", True),
        (JOB.replace("72609", "70000"), "", False),  # another job's card never proves this one
        (JOB, "<button>Confirm</button>", False),  # a pending confirmation is not a submission
    ],
)
async def test_profile_card_for_this_job_is_proof(page, tmp_path, href, extra, proved):
    resolver = Resolver(Profile(), None, None, job={"id": "j", "ats": "oracle", "url": JOB})
    form = FormSession(page, resolver, tmp_path / "resume.pdf", lambda *a: None)
    await serve(page, PROFILE, f"<html><body>{card(href, extra)}</body></html>")
    receipt = await form.proof()
    assert (receipt is not None and receipt["status_region"] == "job_application_status") is proved


async def test_tile_without_job_link_matches_by_requisition_number(page, tmp_path):
    """Texas Instruments 2026-10-14: the tile links elsewhere; its text names the requisition."""
    resolver = Resolver(Profile(), None, None, job={"id": "j", "ats": "oracle", "url": JOB})
    form = FormSession(page, resolver, tmp_path / "resume.pdf", lambda *a: None)
    body = (
        "<ul><li class='cs-application-tile'><a href='#details'>Data Engineer</a>"
        "<div>Ford \u2022 72609 \u2022 Applied on 10/14/2026</div>"
        "<div>Status: Application/Resume Received</div></li></ul>"
    )
    await serve(page, PROFILE, f"<html><body>{body}</body></html>")
    receipt = await form.proof()
    assert receipt is not None and receipt["status_region"] == "job_application_status"
