"""The site's own header search is no open application form (fix-submit, 2026-10-31).

Adzuna 2026-10-31: "Application submitted: Junior Software Engineer" sat under the board's "What? / Where? / Search" header,
whose two text boxes made the confirmation read as an open form, so the accepted application ended unknown. Synthetic."""

from jobpilot.answers import Resolver
from jobpilot.forms import FormSession
from jobpilot.schemas import Profile

SEARCH = (
    "<header><form action='https://board.example.test/search' method='GET'><label for=q>What?</label>"
    "<input type=text name=q id=q><label for=w>Where?</label><input type=text name=w id=w>"
    "<button type=submit>Search</button></form></header>"
)


async def serve(page, body):
    async def route(request):
        await request.fulfill(status=200, content_type="text/html", body=body)

    await page.context.route("**/*", route)
    await page.goto("https://board.example.test/details/1/apply/submit")


def session(page, tmp_path):
    job = {"id": "j", "ats": "adzuna", "url": "https://board.example.test/details/1/apply", "title": "Junior Engineer"}
    return FormSession(page, Resolver(Profile(), None, None, job=job), tmp_path / "resume.pdf", lambda *a: None)


async def test_a_receipt_under_the_site_search_is_read(page, tmp_path):
    await serve(page, f"<html><body>{SEARCH}<main><h1>Application submitted: Junior Engineer</h1></main></body></html>")
    assert await session(page, tmp_path).proof()


async def test_a_form_beside_the_site_search_is_still_a_form(page, tmp_path):
    body = (
        f"<html><body>{SEARCH}<main><h1>Application submitted: Junior Engineer</h1>"
        "<form><input type=text name=firstName></form></main></body></html>"
    )
    await serve(page, body)
    assert await session(page, tmp_path).proof() is None
