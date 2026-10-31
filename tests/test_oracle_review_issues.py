"""Oracle's review page "You have N issues that need to be fixed before you can submit" is a refused commit.

Lazard 2026-10-23: 61 runs on 37 jobs ended as uncertain submissions because only the adapter's poll knew the wording."""


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


async def test_oracles_review_page_with_issues_is_a_refused_commit_that_names_them(page, tmp_path):
    """Lazard 2026-10-23: "You have 2 issues that need to be fixed before you can submit" ended 61 runs as unverified."""
    body = (
        "<html><body><div role='alert'>You have 2 issues that need to be fixed before you can submit your application."
        "</div><ul><li><div>Data Science</div><div>Example State University</div><div>Fields to fix: 2</div></li>"
        "</ul></body></html>"
    )
    await serve(page, ORACLE_JOB + "/apply", body)
    rejection = await session(page, tmp_path).rejection()
    assert rejection["category"] == "validation" and rejection["type"] == "explicit_rejection_page"
    assert rejection["issues"] == ["Data Science / Example State University (Fields to fix: 2)"]
