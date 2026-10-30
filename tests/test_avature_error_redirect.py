"""An Avature posting link that lands on the portal's /Error page ("An error has occurred") is a dead posting, closed
before any account work. MetLife's job (ApplicationMethods?jobId=...), EA's and Bain's answered so, and the engine went
into password resets instead of closing the job (2026-10-30)."""

from types import SimpleNamespace

import httpx
import pytest

from jobpilot import browser

PORTAL = "https://careers.example.test/en_US/ml"
APPLY = PORTAL + "/ApplicationMethods?formValues=&jobId=20496&recommendation=&tags=&user="
DETAIL = PORTAL + "/JobDetail/Software-Engineer/20496"
ERROR = PORTAL + "/Error"


@pytest.mark.parametrize(
    "source,error",
    [
        (APPLY, ERROR),
        (DETAIL, ERROR),
        (PORTAL + "/JobDetail/20496", ERROR),
        (PORTAL + "/JobDetail?jobId=20496&src=Feed", ERROR),
        ("https://careers.example.test/jobs/FolderDetail?folderId=108806", "https://careers.example.test/jobs/Error"),
    ],
)
def test_a_posting_link_redirected_to_the_portals_error_page_is_dead(source, error):
    assert browser.unavailable_posting_redirect(source, error) == "error"


@pytest.mark.parametrize(
    "source",
    [
        PORTAL + "/Login?jobId=20496",
        PORTAL + "/ResetPassword?jobId=20496",
        PORTAL + "/Register",
        PORTAL + "/ApplicationForm?jobId=20496",
        PORTAL + "/ApplicationMethods",  # no job id: it names no posting
        PORTAL + "/JobDetail",
        "https://careers.example.test/",
    ],
)
def test_a_mid_application_page_or_a_link_naming_no_posting_is_not_a_dead_posting(source):
    assert not browser.unavailable_posting_redirect(source, ERROR)


@pytest.mark.parametrize(
    "target",
    [
        "https://other.example.test/en_US/ml/Error",  # another host
        "https://careers.example.test/en_US/other/Error",  # another portal
        ERROR + "?returnUrl=/x",
        PORTAL + "/ErrorReport",
        "http://careers.example.test/en_US/ml/Error",
    ],
)
def test_only_the_same_portals_plain_error_page_counts(target):
    assert not browser.unavailable_posting_redirect(APPLY, target)


def test_a_tenant_address_answering_with_its_employers_domain_error_page_counts():
    tenant = "https://acme.avature.net/en_US/careers/JobDetail/66499"
    assert browser.unavailable_posting_redirect(tenant, "https://careers.acme.example.test/en_US/careers/Error") == "error"
    other = "https://careers.example.test/en_US/careers/JobDetail/66499"
    assert not browser.unavailable_posting_redirect(other, "https://elsewhere.example.test/en_US/careers/Error")


def test_the_error_redirect_chain_of_the_landing_page_closes_it():
    short = SimpleNamespace(url="https://short.example.test/job/7", redirected_from=None)
    posting = SimpleNamespace(url=APPLY, redirected_from=short)
    error = SimpleNamespace(url=ERROR, redirected_from=posting)
    assert browser.unavailable_posting_response(SimpleNamespace(status=200, url=ERROR, request=error)) == "error"
    # An error page reached from a sign-in step, not from the posting, is no verdict.
    login = SimpleNamespace(url=PORTAL + "/Login?jobId=20496", redirected_from=short)
    assert not browser.unavailable_posting_response(
        SimpleNamespace(status=200, url=ERROR, request=SimpleNamespace(url=ERROR, redirected_from=login))
    )


class Client:
    """Answers each GET from `plan[url]` (status, location); counts them."""

    plan, asked = {}, []

    def __init__(self, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def get(self, url, **kwargs):
        assert kwargs == {"follow_redirects": False}
        Client.asked.append(url)
        status, location = Client.plan[url]
        return httpx.Response(status, headers={"location": location} if location else {}, request=httpx.Request("GET", url))


@pytest.fixture
def portal(monkeypatch):
    Client.plan, Client.asked = {}, []
    monkeypatch.setattr(httpx, "AsyncClient", Client)

    async def quickly(seconds):
        pass

    monkeypatch.setattr(browser.asyncio, "sleep", quickly)
    return Client


async def test_the_preflight_closes_a_dead_apply_link_after_asking_twice(portal):
    portal.plan = {APPLY: (302, ERROR)}
    reason = await browser.posting_gone(APPLY)
    assert "error page" in reason and portal.asked == [APPLY, APPLY]


async def test_a_passing_error_page_does_not_close_a_live_posting(portal):
    answers = iter([(302, ERROR), (200, "")])

    class Flaky(Client):
        async def get(self, url, **kwargs):
            Client.plan = {url: next(answers)}
            return await super().get(url, **kwargs)

    httpx.AsyncClient = Flaky  # monkeypatch restores the original after the test
    assert await browser.posting_gone(APPLY) == ""


async def test_a_live_posting_and_a_login_redirect_are_not_closed(portal):
    portal.plan = {DETAIL: (200, ""), APPLY: (302, PORTAL + "/Login?jobId=20496")}
    assert await browser.posting_gone(DETAIL) == "" and await browser.posting_gone(APPLY) == ""


async def test_the_preflight_follows_a_tenant_address_to_its_employers_domain(portal):
    tenant = "https://acme.avature.net/en_US/careers/JobDetail/66499"
    employer = "https://careers.acme.example.test/en_US/careers/JobDetail/66499"
    portal.plan = {
        tenant: (301, employer),
        employer: (302, "https://careers.acme.example.test/en_US/careers/Error"),
    }
    assert "error page" in await browser.posting_gone(tenant)
