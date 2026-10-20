"""Dayforce's candidate portal renders client-side: York Space Systems' early-career postings were skipped
as "no job description content" (2026-10-16). The page's __NEXT_DATA__ jobData carries the posting."""

import json

import httpx

from jobpilot.features.discovery.postings import posting_text

URL = "https://jobs.dayforcehcm.com/en-US/acme/candidateportal/jobs/4268"


def page(posting_id):
    content = {
        "jobDescriptionHeader": "<p>About Acme</p>",
        "jobDescription": "<p>Build &amp; ship flight software.</p>",
        "jobDescriptionFooter": None,
    }
    data = {"props": {"pageProps": {"jobData": {"jobPostingId": posting_id, "jobPostingContent": content}}}}
    return f'<html><body></body><script id="__NEXT_DATA__" type="application/json">{json.dumps(data)}</script></html>'


async def text_for(body, status=200):
    transport = httpx.MockTransport(lambda request: httpx.Response(status, text=body))
    async with httpx.AsyncClient(transport=transport) as client:
        return await posting_text(client, URL)


async def test_dayforce_posting_text_comes_from_the_page_job_data():
    assert await text_for(page(4268)) == "About Acme Build & ship flight software."


async def test_another_posting_or_a_missing_page_is_not_this_job():
    assert await text_for(page(999)) == ""
    assert await text_for("<html>Job Details | Dayforce Jobs</html>") == ""
    assert await text_for(page(4268), status=404) == ""


def test_ranking_replaces_a_page_title_stub_with_the_posting_text(service, monkeypatch):
    import asyncio

    from jobpilot import discovery
    from jobpilot.db import Job, Resume
    from jobpilot.schemas import JobInput

    with service.db.exclusive() as s:
        s.add(Resume(id="r1", name="Main", filename="main.pdf", sha256="x" * 64, text="Python, C++"))
    row, _ = discovery.add_job(
        service.db,
        JobInput(url=URL, title="Flight Software Engineer (Early Career)", company="Acme",
                 description="Job Details | Dayforce Jobs"),
        origin="manual",
    )

    async def posting(client, url, cache=None):
        return "Build and test flight software in C++ and Python. " * 6

    async def structured(config, db, output_type, instructions, prompt, *args, **kwargs):
        return output_type(score=80, eligible=True, resume_id="r1", reason="Strong fit")

    monkeypatch.setattr("jobpilot.features.discovery.postings.posting_text", posting)
    monkeypatch.setattr(discovery, "structured", structured)
    asyncio.run(asyncio.to_thread(discovery.rank_in_thread, service.db, service.config, row["id"]))
    with service.db.session() as s:
        assert s.get(Job, row["id"]).description.startswith("Build and test flight software")


async def test_adp_workforce_now_text_comes_from_its_public_requisition():
    url = "https://workforcenow.adp.com/mascsr/default/mdf/recruitment/recruitment.html?cid=d5ca7713-e342-4b90-89f2-0d351ff60dbb&jobId=593718"

    def route(request):
        assert request.url.path.endswith("/job-requisitions/593718")
        assert request.url.params["cid"] == "d5ca7713-e342-4b90-89f2-0d351ff60dbb"
        return httpx.Response(200, json={"requisitionDescription": "<p>Build data pipelines in Python and SQL.</p>"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(route)) as client:
        assert await posting_text(client, url) == "Build data pipelines in Python and SQL."
