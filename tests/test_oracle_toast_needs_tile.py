"""Oracle's generic "Thank you for your job application" toast proves a job only when the list holds its tile.

28 Oracle receipts since 10-17 rested on the toast alone; the list holds every application of the account. Synthetic."""


from jobpilot.answers import Resolver
from jobpilot.forms import FormSession
from jobpilot.schemas import Profile

ORACLE_JOB = "https://acme.fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/job/4711"
ORACLE_LIST = "https://acme.fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/my-profile"


async def serve(page, url, body):
    async def route(request):
        await request.fulfill(status=200, content_type="text/html", body=body)

    await page.context.route("**/*", route)
    await page.goto(url)


def session(page, tmp_path, url=ORACLE_JOB, title="Data Analyst"):
    resolver = Resolver(Profile(), None, None, job={"id": "j", "ats": "oracle", "url": url, "title": title})
    return FormSession(page, resolver, tmp_path / "resume.pdf", lambda *a: None)


TOAST = "<div role='alert'>Thank you for your job application.</div>"


async def test_oracles_generic_toast_proves_a_job_only_when_the_list_holds_its_tile(page, tmp_path):
    other = "<ul><li class='cs-application-tile'><a href='/job/1'>Warehouse Lead</a><div>Applied on 10/10/2026</div></li></ul>"
    await serve(page, ORACLE_LIST, f"<html><body>{TOAST}{other}</body></html>")
    assert await session(page, tmp_path, url=ORACLE_JOB).proof() is None


async def test_oracles_toast_with_this_jobs_tile_still_proves_it(page, tmp_path):
    """Ascension 2026-10: the tile links to another host, so only its title says whose it is."""
    mine = "<ul><li class='cs-application-tile'><a href='/x'>Data Analyst</a><div>Applied on 10/10/2026</div></li></ul>"
    await serve(page, ORACLE_LIST, f"<html><body>{TOAST}{mine}</body></html>")
    form = session(page, tmp_path, url="https://jobs.example.test/us/en/job/24746")
    receipt = await form.proof()
    assert receipt and receipt["status_region"] == "application_acknowledgement"
