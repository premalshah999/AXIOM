"""Cisco, Visa, Applied Materials and AIG (2026-10-18) answered "page does not exist" while a static new-grad list kept
the link alive. The employer's own Workday search confirms it: a requisition it no longer lists is closed after one
run, instead of waiting as a failure for a second run that never came."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from jobpilot import workday
from jobpilot.workday import WORKDAY_POSTING, missing_posting_result, tenant_search_lists

URLS = {
    "https://amat.wd1.myworkdayjobs.com/External/job/Santa-ClaraCA/New-College-Grad-Software-Engineer-I--Bachelors---Santa-Clara--CA-_R2628998": ("amat", "wd1", "External", "R2628998"),
    "https://visa.wd5.myworkdayjobs.com/en-US/visa_talent_portal/job/US---Foster-City-CA/Software-Engineer--New-College-Grad---Foster-City_REF085423W": ("visa", "wd5", "visa_talent_portal", "REF085423W"),
    "https://cisco.wd5.myworkdayjobs.com/cisco_careers/job/RTP-North-Carolina-US/Software-Engineer_2026779-1": ("cisco", "wd5", "cisco_careers", "2026779"),
}


@pytest.mark.parametrize("url,parts", URLS.items())
def test_the_requisition_is_read_from_the_posting_url(url, parts):
    assert WORKDAY_POSTING.match(url).groups() == parts


def test_a_requisition_the_employer_no_longer_lists_is_closed_after_one_run(service):
    job = {"id": "gone", "url": next(iter(URLS))}
    assert missing_posting_result(service.db, job, "run-1", job["url"], listed=False)["state"] == "closed"
    assert missing_posting_result(service.db, {**job, "id": "kept"}, "run-1", job["url"])["state"] == "waiting_browser"


@pytest.mark.parametrize("payload,expected", [
    ({"total": 0, "jobPostings": []}, False),
    ({"total": 1, "jobPostings": [{"externalPath": "/job/Santa-ClaraCA/New-College-Grad_R2628998"}]}, True),
    ({"total": 3, "jobPostings": [{"externalPath": "/job/Elsewhere/Other_R1111111"}]}, None),
])
async def test_the_tenant_search_decides_listed_or_unknown(monkeypatch, payload, expected):
    class Response:
        status_code = 200

        def json(self):
            return payload

    class Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, json):
            assert json["searchText"] == "R2628998" and url.endswith("/wday/cxs/amat/External/jobs")
            return Response()

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", Client)
    assert await tenant_search_lists(next(iter(URLS))) is expected


def test_a_requisition_the_employer_still_lists_is_never_closed_by_a_not_found_page(service):
    # Disney, Cisco, Toyota, Motorola, Applied Materials (2026-10-14..29): two runs saw "page does not exist" at the
    # posting URL and closed it; the employer's own search lists all of them today.
    job = {"id": "live", "url": next(iter(URLS))}
    for run in ("run-1", "run-2", "run-3"):
        result = missing_posting_result(service.db, job, run, job["url"], listed=True)
        assert result["state"] == "waiting_browser" and result["recovery_cause"] == "posting_unavailable"


class Engine:
    """A browser engine on a Workday posting whose not-found page the stall handlers have just read."""

    def __init__(self, service, listed, ats="workday"):
        from jobpilot.browser import BrowserEngine

        self.db, self.run_id, self.page = service.db, "run-1", SimpleNamespace(url=next(iter(URLS)))
        self.job = {"id": "stalled", "url": next(iter(URLS)), "ats": ats}
        self.form = SimpleNamespace(scan=AsyncMock(return_value={
            "fields": [], "headings": [], "errors": [], "text": "The page you are looking for doesn't exist."}))
        self.armed = False
        self.listed = listed
        self.page_closed = lambda: BrowserEngine.page_closed(self)
        self.posting_closure = lambda *a: BrowserEngine.posting_closure(self, *a)


@pytest.mark.parametrize("listed,closed", [(True, False), (None, False), (False, True)])
async def test_a_stalled_workday_not_found_page_closes_only_on_the_employers_own_search(service, monkeypatch, listed, closed):
    # HNTB, Moffitt, Nordstrom, Varian, WVU Medicine, Evolent (2026-10-29): page_closed closed each on its first sight.
    monkeypatch.setattr(workday, "tenant_search_lists", AsyncMock(return_value=listed))
    result = await Engine(service, listed).page_closed()
    assert (result is not None and result["state"] == "closed") is closed


async def test_another_platforms_not_found_page_still_closes_at_once(service):
    result = await Engine(service, None, ats="custom").page_closed()
    assert result["state"] == "closed" and "does" in result["reason"]
