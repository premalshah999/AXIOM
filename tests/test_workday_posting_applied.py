"""State Street (Workday) 2026-10-17: a second job record for the same requisition (R-789867, Quincy and Princeton)
opened a posting page that said "You applied for this job on October 17, 2026." with View Application and no Apply.
The account loop recognised none of its states for the whole setup window and stalled; it is already applied."""

from types import SimpleNamespace

from jobpilot.workday import WorkdayAuth

URL = "https://statestreet.wd1.myworkdayjobs.com/en-US/Global/job/Quincy-Massachusetts/Software-Engineer_R-789867-1"


async def posting(page, body):
    async def handler(route):
        await route.fulfill(status=200, content_type="text/html", body=body)

    await page.route("https://statestreet.wd1.myworkdayjobs.com/**", handler)
    await page.goto(URL)
    return WorkdayAuth(SimpleNamespace(job={"url": URL}, page=page))


async def test_the_posting_page_of_a_submitted_application_is_already_applied(page):
    auth = await posting(page, "<h2>Software Engineer</h2><p>You applied for this job on October 17, 2026.</p>"
                               "<button>View Application</button>")
    result = await auth.posting_already_applied()
    assert result["state"] == "already_applied"
    assert result["receipt"]["evidence"].startswith("You applied for this job on")


async def test_an_open_posting_or_sign_in_footer_is_not(page):
    auth = await posting(page, "<h2>Software Engineer</h2><button>Apply</button><p>Already applied? Sign in</p>")
    assert await auth.posting_already_applied() is None
