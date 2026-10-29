from types import SimpleNamespace

import httpx
import pytest

from jobpilot import browser

POSTING = 'https://careers.example.test/en_US/careers/JobDetail/Engineer/12345'
SEARCH = 'https://careers.example.test/en_US/careers/SearchJobs'


def test_short_link_chain_preserves_the_employers_closed_posting_redirect():
    short = SimpleNamespace(url='https://short.example.test/job/7', redirected_from=None)
    job = SimpleNamespace(url=POSTING, redirected_from=short)
    search = SimpleNamespace(url=SEARCH, redirected_from=job)
    assert browser.unavailable_posting_response(SimpleNamespace(status=200, url=SEARCH, request=search))


@pytest.mark.parametrize('target', [
    'https://different.example.test/en_US/careers/SearchJobs',
    'https://careers.example.test/en_US/other/SearchJobs',
    'https://careers.example.test/en_US/careers/Login',
    SEARCH + '?jobId=12345',
    POSTING,
])
def test_authentication_other_portals_and_filtered_search_are_not_closure(target):
    assert not browser.unavailable_posting_redirect(POSTING, target)


def test_a_plain_search_link_is_not_a_closed_posting():
    assert not browser.unavailable_posting_redirect('https://short.example.test/job/7', SEARCH)
    assert not browser.unavailable_posting_response(None)


@pytest.mark.parametrize('status,location,closed', [(302, SEARCH, True), (200, '', False), (302, '/en_US/careers/Login', False)])
async def test_avature_preflight_checks_the_posting_without_signing_in(monkeypatch, status, location, closed):
    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def get(self, url, **kwargs):
            assert kwargs == {'follow_redirects': False}
            return httpx.Response(status, headers={'location': location}, request=httpx.Request('GET', url))
    monkeypatch.setattr(httpx, 'AsyncClient', Client)
    assert bool(await browser.posting_gone(POSTING)) == closed
