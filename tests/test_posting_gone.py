"""Dead Greenhouse and Lever postings close before the browser opens: Toast's redirected to its careers homepage and
Gas South's and Sparksoft's boards no longer listed the role, and each stalled as a repair (2026-10-26)."""

import httpx
import pytest

from jobpilot import browser


class Client:
    def __init__(self, status, seen):
        self.status, self.seen = status, seen

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url):
        self.seen.append(url)
        return httpx.Response(self.status, request=httpx.Request("GET", url))


@pytest.mark.parametrize("url,api", [
    ("https://job-boards.greenhouse.io/toast/jobs/8224111", "https://boards-api.greenhouse.io/v1/boards/toast/jobs/8224111"),
    ("https://boards.greenhouse.io/embed/job_app?for=acme&token=123", "https://boards-api.greenhouse.io/v1/boards/acme/jobs/123"),
    # EU boards are served by the same public API (Coherent Solutions 2026-10-26 stalled twice as a repair).
    ("https://job-boards.eu.greenhouse.io/acme/jobs/5003468101",
     "https://boards-api.greenhouse.io/v1/boards/acme/jobs/5003468101"),
    ("https://jobs.lever.co/acme/2f6c6a62-1111-4c3e-9a0b-0123456789ab",
     "https://api.lever.co/v0/postings/acme/2f6c6a62-1111-4c3e-9a0b-0123456789ab"),
])
async def test_a_404_from_the_public_api_closes_the_posting(monkeypatch, url, api):
    seen = []
    monkeypatch.setattr(httpx, "AsyncClient", lambda **k: Client(404, seen))
    assert "no longer lists it" in await browser.posting_gone(url) and seen == [api]


async def test_a_live_posting_or_other_platform_is_no_verdict(monkeypatch):
    seen = []
    monkeypatch.setattr(httpx, "AsyncClient", lambda **k: Client(200, seen))
    assert await browser.posting_gone("https://job-boards.greenhouse.io/lyft/jobs/1") == ""
    assert await browser.posting_gone("https://acme.wd5.myworkdayjobs.com/en-US/careers/job/x") == ""
