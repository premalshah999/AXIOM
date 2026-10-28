"""TikTok's job page (2026-10-27): "Discover a career that energizes and excites you every day." is the site's search
box beside "Search now", under "Apply to this job". Read as an application question, the job page became a form with
no way forward and the guided controller took the run until its recovery budget ran out."""

from jobpilot.forms import SCAN, site_search

JOB_PAGE = """
<h1>Machine Learning Engineer</h1>
<a href="/resume/1/apply"><button type=button>Apply to this job</button></a>
<p>Discover a career that energizes and excites you every day.</p>
<div data-testid=search-box><div><img alt="search icon" width=32 height=32>
 <input type=text aria-label="Discover a career that energizes and excites you every day."></div>
 <button type=button>Search now</button></div>
"""

EMAIL_STEP = """
<form><label for=e>Email address</label><input id=e type=text><button type=submit>Next</button></form>
<form><label for=c>City</label><input id=c type=text><label for=z>Zip</label><input id=z type=text>
 <button type=button>Search</button></form>
"""


async def test_a_lone_box_beside_search_now_is_the_sites_search(page):
    await page.set_content(JOB_PAGE)
    fields = (await page.evaluate(SCAN))["fields"]
    assert [f["search_box"] for f in fields] == [True]
    assert site_search(fields[0])


async def test_an_application_box_is_never_a_site_search(page):
    await page.set_content(EMAIL_STEP)
    fields = (await page.evaluate(SCAN))["fields"]
    assert [f["label"] for f in fields] == ["Email address", "City", "Zip"]
    assert not any(f["search_box"] or site_search(f) for f in fields)
