"""Cisco, Visa, Applied Materials and AIG (2026-10-18) answered "page does not exist" while a static new-grad list kept
the link alive. The employer's own Workday search confirms it: a requisition it no longer lists is closed after one
run, instead of waiting as a failure for a second run that never came."""

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
