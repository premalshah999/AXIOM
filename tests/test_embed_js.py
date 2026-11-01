"""What EMBED_JS collects from a live page (2026-10-31): iframes, Apply links and scripts as before, and now the meta tags,
stylesheets and marker elements that name a platform whose URLs the page never shows (Microsoft's and AARP's Eightfold,
Two Sigma's Avature, the Greenhouse holder Abnormal and Databricks fill after the load event)."""

from jobpilot.ats import EMBED_JS, classify_embedded

PAGE = """<!doctype html><html><head>
<meta name="avature.portal.id" content="106"><meta property="og:title" content="Engineer">
<meta name="viewport" content="width=device-width">
<link rel="stylesheet" href="https://static.vscdn.net/fonts/css/eightfold-font-base.css">
<link rel="canonical" href="https://boards.greenhouse.io/acme/jobs/1">
<link rel="icon" href="https://cdn.example.test/favicon.ico">
<script src="https://cdn.phenompeople.com/phenom.js"></script></head>
<body><div id="grnhse_app"></div><div id="other"></div>
<a href="https://acme.wd3.myworkdayjobs.com/en/job/1/apply">Apply now</a>
<a href="https://example.test/about">About us</a>
<iframe src="https://boards.greenhouse.io/embed/job_app?for=acme&token=1"></iframe></body></html>"""


async def test_the_page_scan_collects_every_kind_of_fingerprint(page):
    await page.set_content(PAGE)
    entries = [tuple(e) for e in await page.evaluate(EMBED_JS)]
    kinds = {}
    for kind, value in entries:
        kinds.setdefault(kind, []).append(value)
    assert kinds["frame"] == ["https://boards.greenhouse.io/embed/job_app?for=acme&token=1"]
    assert kinds["apply"] == ["https://acme.wd3.myworkdayjobs.com/en/job/1/apply"]
    assert kinds["script"] == ["https://cdn.phenompeople.com/phenom.js"]
    assert "avature.portal.id=106" in kinds["meta"] and "og:title=Engineer" in kinds["meta"]
    # Stylesheets only: a canonical or icon link says nothing about who serves the page.
    assert kinds["link"] == ["https://static.vscdn.net/fonts/css/eightfold-font-base.css"]
    assert kinds["dom"] == ["grnhse_app"]
    # The frame, the form itself, outranks every page-level hint.
    assert classify_embedded(entries)[0] == "greenhouse"


async def test_a_page_with_only_page_level_hints_is_read_from_them(page):
    await page.set_content(
        '<head><meta name="avature.portal.page" content="JobDetail"></head><body><a href="/apply">Apply</a></body>'
    )
    assert classify_embedded([tuple(e) for e in await page.evaluate(EMBED_JS)]) == ("avature", None, "meta")


async def test_the_page_scan_collects_forms_and_the_postings_structured_data(page):
    # Forms that post to a platform and a JobPosting's own links (2026-11-01): the application's address when the page
    # shows no frame, matching entries_from_html on the same markup.
    from jobpilot.ats import entries_from_html

    html = (
        '<head><script type="application/ld+json">{"@type":"JobPosting","url":"https://jobs.lever.co/acme/'
        '0f5d2c1e-1111-2222-3333-444455556666"}</script></head>'
        '<body><form action="https://boards.greenhouse.io/acme/jobs/1/apply"></form><a href="/x">Start your application</a></body>'
    )
    await page.set_content(html)
    live = {(k, v) for k, v in (tuple(e) for e in await page.evaluate(EMBED_JS)) if k in {"form", "jsonld", "apply"}}
    static = {(k, v) for k, v in entries_from_html(html, "about:blank") if k in {"form", "jsonld", "apply"}}
    assert ("form", "https://boards.greenhouse.io/acme/jobs/1/apply") in live
    assert ("jsonld", "https://jobs.lever.co/acme/0f5d2c1e-1111-2222-3333-444455556666") in live
    assert {k for k, _ in live} == {"form", "jsonld", "apply"}
    assert {k for k, _ in static} == {k for k, _ in live}
