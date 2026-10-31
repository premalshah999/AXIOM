"""Cisco, Visa, Applied Materials and AIG (2026-10-18) answered "page does not exist" while a static new-grad list kept
the link alive. The employer's own Workday search confirms it: a requisition it no longer lists is closed after one
run, instead of waiting as a failure for a second run that never came."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from jobpilot import workday
from jobpilot.workday import POSTING_SEARCH_ATTEMPTS, WORKDAY_POSTING, missing_posting_result, tenant_search_lists

URLS = {
    "https://amat.wd1.myworkdayjobs.com/External/job/Santa-ClaraCA/New-College-Grad-Software-Engineer-I--Bachelors---Santa-Clara--CA-_R2628998": ("amat", "wd1", "External", "R2628998"),
    "https://visa.wd5.myworkdayjobs.com/en-US/visa_talent_portal/job/US---Foster-City-CA/Software-Engineer--New-College-Grad---Foster-City_REF085423W": ("visa", "wd5", "visa_talent_portal", "REF085423W"),
    "https://cisco.wd5.myworkdayjobs.com/cisco_careers/job/RTP-North-Carolina-US/Software-Engineer_2026779-1": ("cisco", "wd5", "cisco_careers", "2026779"),
    # Hyphenated requisitions (State Street, Humana, AT&T, Lilly, US Bank, 2026-10): read as "R", the search asked for
    # every R- posting, "listed" came back true for gone postings and unreadable for the ones with a repost counter.
    "https://statestreet.wd1.myworkdayjobs.com/en-US/global/job/Quincy-Massachusetts/Junior-Software-Engineer_R-797359-2": ("statestreet", "wd1", "global", "R-797359"),
    "https://att.wd1.myworkdayjobs.com/ATTGeneral/job/USATXDallas/AI-Native-Software-Engineer_R-124488": ("att", "wd1", "ATTGeneral", "R-124488"),
    "https://usbank.wd1.myworkdayjobs.com/US_Bank_Careers/job/Minneapolis-MN/Software-Engineer-1---Python_2026-0033403-1": ("usbank", "wd1", "US_Bank_Careers", "2026-0033403"),
}


@pytest.mark.parametrize("url,parts", URLS.items())
def test_the_requisition_is_read_from_the_posting_url(url, parts):
    assert WORKDAY_POSTING.match(url).groups() == parts


def test_a_requisition_the_employer_no_longer_lists_is_closed_after_one_run(service):
    job = {"id": "gone", "url": next(iter(URLS))}
    assert missing_posting_result(service.db, job, "run-1", job["url"], listed=False)["state"] == "closed"
    assert missing_posting_result(service.db, {**job, "id": "kept"}, "run-1", job["url"])["state"] == "waiting_browser"


def fake_search(monkeypatch, response=None, error=None, expect=None):
    """httpx's client answering the tenant search with `response` (status, body), or raising `error`."""
    class Response:
        def __init__(self, status, body):
            self.status_code, self.body = status, body

        def json(self):
            if isinstance(self.body, Exception):
                raise self.body
            return self.body

    class Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, json):
            if expect:
                assert json["searchText"] == expect
            if error:
                raise error
            return Response(*response)

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", Client)


GONE = {"total": 0, "jobPostings": []}
LISTED = {"total": 1, "jobPostings": [{"externalPath": "/job/Santa-ClaraCA/New-College-Grad_R2628998"}]}
OTHER = {"total": 3, "jobPostings": [{"externalPath": "/job/Elsewhere/Other_R1111111"}]}


@pytest.mark.parametrize("payload,expected", [(GONE, False), (LISTED, True), (OTHER, None)])
async def test_the_tenant_search_decides_listed_or_unknown(monkeypatch, payload, expected):
    fake_search(monkeypatch, (200, payload), expect="R2628998")
    assert await tenant_search_lists(next(iter(URLS))) is expected


@pytest.mark.parametrize("response,error", [
    ((500, GONE), None),  # the tenant is failing
    ((429, GONE), None),
    ((200, ["not", "a", "posting", "list"]), None),
    ((200, {"errorCode": "maintenance"}), None),
    ((200, {"total": 0}), None),  # no posting list at all: nothing was said about this posting
    ((200, {"total": 0, "jobPostings": None}), None),
    ((200, ValueError("<html>Workday is down</html>")), None),
    (None, TimeoutError("10 s")),
])
async def test_a_search_that_errors_or_is_unreadable_says_nothing_about_the_posting(monkeypatch, response, error):
    # Only "no hits for the requisition" is the employer's answer; every other outcome is unknown, never "gone".
    fake_search(monkeypatch, response, error)
    assert await tenant_search_lists(next(iter(URLS))) is None


async def test_a_hyphenated_requisition_is_searched_whole_and_not_mistaken_for_its_prefix(monkeypatch):
    # State Street, Lilly, Leidos, Amgen... (2026-10-31): the requisition "R-797359" was searched as "R", and any other
    # "R-" posting made the gone posting look listed (18 of 23 postings thought wrongly closed were gone).
    url = "https://statestreet.wd1.myworkdayjobs.com/en-US/global/job/Quincy-Massachusetts/Junior-Software-Engineer_R-797359-2"
    other = {"total": 2, "jobPostings": [{"externalPath": "/job/Quincy/Other-Role_R-111111"}, {"externalPath": "/job/Boston/Role_R-222222-1"}]}
    fake_search(monkeypatch, (200, other), expect="R-797359")
    assert await tenant_search_lists(url) is None
    fake_search(monkeypatch, (200, {"total": 1, "jobPostings": [{"externalPath": "/job/Quincy/Junior-Software-Engineer_R-797359-1"}]}), expect="R-797359")
    assert await tenant_search_lists(url) is True
    fake_search(monkeypatch, (200, GONE), expect="R-797359")
    assert await tenant_search_lists(url) is False


def test_a_requisition_the_employer_still_lists_is_never_closed_by_a_not_found_page(service):
    # Disney, Cisco, Toyota, Motorola, Applied Materials (2026-10-14..29): two runs saw "page does not exist" at the
    # posting URL and closed it; the employer's own search lists all of them today.
    job = {"id": "live", "url": next(iter(URLS))}
    for run in ("run-1", "run-2", "run-3"):
        result = missing_posting_result(service.db, job, run, job["url"], listed=True)
        assert result["state"] == "waiting_browser" and result["recovery_cause"] == "posting_unavailable"


def test_a_search_that_cannot_answer_never_closes_the_posting_however_often_it_happens(service):
    # 88 Workday postings were closed "no longer available" since 2026-10-17; with the search erroring or unreadable
    # (`listed` None) the second run at the not-found page closed the job. It is tried again later, and after
    # POSTING_SEARCH_ATTEMPTS runs it stops on a reason that says its availability is unknown.
    job = {"id": "unknown", "url": next(iter(URLS))}
    results = [missing_posting_result(service.db, job, f"run-{n}", job["url"], listed=None) for n in range(1, 8)]
    assert all(r["state"] == "waiting_browser" for r in results)
    early, final = results[:POSTING_SEARCH_ATTEMPTS - 1], results[POSTING_SEARCH_ATTEMPTS - 1:]
    assert all(r["recovery_cause"] == "site_temporary" and "tried again later" in r["reason"] for r in early)
    assert all(r["recovery_cause"] == "posting_unavailable" and "not closed" in r["reason"] for r in final)
    # A repeat of the same run is not another attempt.
    job = {"id": "repeat", "url": next(iter(URLS))}
    assert all(missing_posting_result(service.db, job, "run-1", job["url"], listed=None)["recovery_cause"] == "site_temporary" for _ in range(5))


def test_the_employers_own_zero_hits_still_closes_after_unknown_runs(service):
    job = {"id": "gone-later", "url": next(iter(URLS))}
    missing_posting_result(service.db, job, "run-1", job["url"], listed=None)
    assert missing_posting_result(service.db, job, "run-2", job["url"], listed=False)["state"] == "closed"


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
