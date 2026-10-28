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
    ("https://apply.workable.com/acme-inc/j/C4B8B72648/apply",
     "https://apply.workable.com/api/v2/accounts/acme-inc/jobs/C4B8B72648"),
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


class Replies(Client):
    """Answers each GET with the next (status, json) pair."""

    def __init__(self, replies, seen):
        self.replies, self.seen = list(replies), seen

    async def get(self, url):
        self.seen.append(url)
        status, body = self.replies.pop(0)
        return httpx.Response(status, json=body, request=httpx.Request("GET", url))


async def test_a_smartrecruiters_posting_under_a_renamed_company_that_is_inactive_is_closed(monkeypatch):
    # Crown Innovations (2026-10-26): the old identifier's link opened SmartRecruiters' homepage.
    seen = []
    moved = {"message": "Provided companyIdentifier (TBD1Acme) does not match companyIdentifier (Acme) from publication."}
    monkeypatch.setattr(httpx, "AsyncClient", lambda **k: Replies([(400, moved), (200, {"active": False})], seen))
    assert "no longer lists it" in await browser.posting_gone("https://jobs.smartrecruiters.com/TBD1Acme/744000144160949")
    assert seen[-1] == "https://api.smartrecruiters.com/v1/companies/Acme/postings/744000144160949"


async def test_an_active_smartrecruiters_posting_is_no_verdict(monkeypatch):
    monkeypatch.setattr(httpx, "AsyncClient", lambda **k: Replies([(200, {"active": True})], []))
    assert await browser.posting_gone("https://jobs.smartrecruiters.com/Acme/744000154977749-data-engineer") == ""


class Paths(Client):
    """Answers by URL suffix."""

    def __init__(self, answers, seen):
        self.answers, self.seen = answers, seen

    async def get(self, url):
        self.seen.append(url)
        status = next((code for tail, code in self.answers.items() if url.endswith(tail)), 404)
        return httpx.Response(status, json={}, request=httpx.Request("GET", url))


async def test_a_gh_jid_page_finds_its_board_by_domain(monkeypatch):
    # SeatGeek (2026-10-26): seatgeek.com/jobs/<id>?gh_jid=<id> opened the consumer homepage.
    monkeypatch.setattr(httpx, "AsyncClient", lambda **k: Paths({"/boards/seatgeek/jobs/8248135": 200}, []))
    assert await browser.greenhouse_board("https://seatgeek.com/jobs/8248135?gh_jid=8248135", "SeatGeek") == (
        "seatgeek", "8248135", True)


async def test_a_gh_jid_job_its_board_no_longer_lists_is_closed(monkeypatch):
    monkeypatch.setattr(httpx, "AsyncClient", lambda **k: Paths({"/boards/seatgeek": 200}, []))
    assert "no longer lists it" in await browser.posting_gone("https://seatgeek.com/jobs/8247550?gh_jid=8247550", "SeatGeek")


async def test_an_unknown_board_is_no_verdict(monkeypatch):
    monkeypatch.setattr(httpx, "AsyncClient", lambda **k: Paths({}, []))
    assert await browser.posting_gone("https://careers.example.test/jobs?gh_jid=123", "Example Co") == ""


class Redirect(Client):
    """Answers the posting URL itself, as Oracle's career site does."""

    def __init__(self, status, location, seen):
        self.status, self.location, self.seen = status, location, seen

    async def get(self, url, **kwargs):
        self.seen.append((url, kwargs.get("follow_redirects")))
        headers = {"location": self.location} if self.location else {}
        return httpx.Response(self.status, headers=headers, request=httpx.Request("GET", url))


ORACLE = "https://acme.fa.us6.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/job/2026911"


async def test_an_oracle_posting_its_site_redirects_to_page_not_found_is_closed(monkeypatch):
    # Hearst (2026-10-27): every career site inactive, the link answered 302 /errors/404 in 14 runs.
    seen = []
    error = "https://acme.fa.us6.oraclecloud.com/hcmUI/CandidateExperience/errors/404"
    monkeypatch.setattr(httpx, "AsyncClient", lambda **k: Redirect(302, error, seen))
    assert "no longer serves it" in await browser.posting_gone(ORACLE) and seen == [(ORACLE, False)]


@pytest.mark.parametrize("status,location", [
    (200, ""),
    (302, "https://acme.fa.us6.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/job/2026911/"),
    (503, ""),
])
async def test_a_served_oracle_posting_is_no_verdict(monkeypatch, status, location):
    monkeypatch.setattr(httpx, "AsyncClient", lambda **k: Redirect(status, location, []))
    assert await browser.posting_gone(ORACLE) == ""
