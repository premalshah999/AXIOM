"""A company's icon, kept on this computer: only an address that vouches for the company asks anything, and a miss shows its letter."""

import httpx
import pytest

from jobpilot import logos
from jobpilot.logos import domain_for, logo

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 40


@pytest.mark.parametrize(
    "company,url,domain",
    [
        ("Two Sigma", "https://careers.twosigma.com/careers/x", "twosigma.com"),
        ("Toyota", "https://careers.toyota.com/us/en/job/1", "toyota.com"),
        ("Farmers Insurance", "https://jobs.farmersinsurance.com/job/1", "farmersinsurance.com"),
        ("Toyota", "https://toyota.wd503.myworkdayjobs.com/tmna/job/1", "toyota.com"),
        ("OpenTable", "https://job-boards.greenhouse.io/opentable/jobs/1", "opentable.com"),
        ("PepsiCo", "https://uscareers-pepsico.icims.com/jobs/1", "pepsico.com"),
        ("BerryDunn", "https://careers-berrydunn.icims.com/jobs/1", "berrydunn.com"),
        ("Example Labs, Inc.", "https://jobs.ashbyhq.com/example/1", "example.com"),
        ("Example", "https://careers.example.co.uk/job/1", "example.co.uk"),
    ],
)
def test_an_address_that_names_the_company_gives_its_domain(company, url, domain):
    assert domain_for(company, url) == domain


@pytest.mark.parametrize(
    "company,url",
    [
        ("WonderBotz", "https://www.adzuna.com/details/1"),  # A job board's own icon would be the wrong logo.
        ("Acme", "https://go.short.example/x"),
        ("Acme", "https://other.wd5.myworkdayjobs.com/x"),
        ("Acme", "https://job-boards.greenhouse.io/other/jobs/1"),
        ("", "https://careers.acme.com/1"),
        ("Acme", ""),
        ("Acme", "not a url"),
        ("A", "https://a.com/"),
    ],
)
def test_anything_else_asks_nobody(company, url):
    assert domain_for(company, url) is None


@pytest.fixture
def fetches(monkeypatch):
    calls = []

    def serving(result):
        async def fetch(domain):
            calls.append(domain)
            if isinstance(result, Exception):
                raise result
            return result

        monkeypatch.setattr(logos, "_fetch", fetch)

    return calls, serving


async def test_a_logo_is_fetched_once_and_kept(tmp_path, fetches):
    calls, serving = fetches
    serving(PNG)
    assert await logo(tmp_path, "example.com") == PNG
    assert await logo(tmp_path, "example.com") == PNG
    assert calls == ["example.com"]
    assert (tmp_path / "logos" / "example.com.png").read_bytes() == PNG


async def test_a_company_with_no_logo_is_not_asked_again_soon(tmp_path, fetches):
    calls, serving = fetches
    serving(None)
    assert await logo(tmp_path, "example.com") is None
    assert await logo(tmp_path, "example.com") is None
    assert calls == ["example.com"]


async def test_being_offline_is_remembered_as_nothing(tmp_path, fetches):
    calls, serving = fetches
    serving(httpx.ConnectError("offline"))
    assert await logo(tmp_path, "example.com") is None
    assert not (tmp_path / "logos" / "example.com.none").exists()
    serving(PNG)
    assert await logo(tmp_path, "example.com") == PNG


@pytest.mark.parametrize("bad", ["../../secret", "a/b.com", "localhost", "", "exa mple.com"])
async def test_only_a_plain_domain_reaches_the_disk_or_the_network(tmp_path, fetches, bad):
    calls, serving = fetches
    serving(PNG)
    assert await logo(tmp_path, bad) is None
    assert calls == []
