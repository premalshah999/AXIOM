"""A host that serves many employers must not stand for one of them in the employer cap.

A feed's link shortener was recorded as the tenant of the first employer reached through it: every posting opened by
a short link counted as that employer's, 88 applications filled its cap, and the next posting from any company
reached the same way was refused as its 89th (2026-11-01). Shared career-site platforms had the same mapping."""

import pytest

from jobpilot.application_pacing import employer_key
from jobpilot.features.knowledge.employers import domain_fits, tenant_alias

SHORT = "https://go.shortener.test/Ab"


def test_a_short_link_names_no_employer_whoever_the_company_is():
    assert tenant_alias(SHORT, "ExampleSoft Inc") is None
    assert tenant_alias(SHORT, "Other Labs") is None
    # Both postings reached through the same short link are two employers for the cap.
    rows = {"domain:shortener.test": ("emp:examplesoft", "tenant"), "examplesoft": ("emp:examplesoft", "name")}
    assert employer_key(rows, "ExampleSoft Inc", SHORT) == "emp:examplesoft"
    assert employer_key(rows, "Other Labs", SHORT) == "name:other labs"


@pytest.mark.parametrize(
    "url,company,expected",
    [
        ("https://careers.examplesoft.test/jobs/1", "ExampleSoft Inc", "domain:examplesoft.test"),
        ("https://jobs.examplesoft.test/1", "ExampleSoft", "domain:examplesoft.test"),
        ("https://jobs.ex.test/1", "Electronic Xylophones", "domain:ex.test"),  # the name's initials
        ("https://tenant1.applyhere.test/jobs/1", "Tenant One Corp", None),  # a shared platform's host
        ("https://careers.examplesoft.test/1", "", "domain:examplesoft.test"),  # no company: the host stands
    ],
)
def test_a_host_names_an_employer_only_when_it_is_the_companys_own(url, company, expected):
    assert tenant_alias(url, company) == expected


def test_domain_fit_follows_the_companys_name():
    assert domain_fits("acme.test", "Acme Corporation")
    assert not domain_fits("shortener.test", "Acme Corporation")
