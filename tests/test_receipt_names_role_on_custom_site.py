"""Receipt wording that names the role, and the employer's "already submitted" notice (fix-submit, 2026-10-31).

Gem/Imagen 2026-10-28: "Your application for Software Engineer has been received!" parked a run on a custom site. Shield AI
2026-10-29: "Your application was already submitted. We received your previous application" is no receipt. Synthetic."""


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


async def test_a_receipt_naming_the_role_is_read_on_a_custom_site(page, tmp_path):
    body = "<html><body><h2>Your application for Software Engineer has been received!</h2><p>Req ID R512</p></body></html>"
    await serve(page, "https://careers.example.test/apply/done", body)
    assert await session(page, tmp_path, url="https://careers.example.test/apply/1").proof()


async def test_the_role_wording_is_no_receipt_on_a_form(page, tmp_path):
    body = "<html><body><h2>Your application for Software Engineer has been received!</h2><input name='email'></body></html>"
    await serve(page, "https://careers.example.test/apply/done", body)
    assert await session(page, tmp_path, url="https://careers.example.test/apply/1").proof() is None


async def test_an_already_submitted_notice_is_an_already_applied_rejection_not_a_receipt(page, tmp_path):
    """Shield AI 2026-10-29: the page quotes the earlier application's date."""
    body = (
        "<html><body><h1>Application</h1><p>Your application was already submitted. We received your previous "
        "application on October 23, 2026.</p></body></html>"
    )
    await serve(page, "https://careers.example.test/apply/done", body)
    form = session(page, tmp_path, url="https://careers.example.test/apply/1")
    assert await form.proof() is None
    assert (await form.rejection())["category"] == "already_applied"
