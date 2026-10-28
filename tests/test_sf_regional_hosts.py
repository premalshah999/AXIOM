"""Wipro (2026-10-27): its SuccessFactors account and passcode pages are on career55.sapsf.eu, one of SAP's newer EU data
centres. The passcode mail matched, then the code box was refused as "an unexpected website" on every run, because
sapsf.eu was missing from the platform's hosts (sapsf.com and successfactors.eu were there)."""

from types import SimpleNamespace

from jobpilot.accounts.credentials import Accounts
from jobpilot.accounts.tenants import account_tenant
from jobpilot.accounts.tenants import sf_company
from jobpilot.mail.senders import KNOWN_SENDERS

JOB = "https://careers.example.test/job/Seattle-Engineer-WA-98101/210722-en_US"


def creds():
    value = object.__new__(Accounts)
    value.config = SimpleNamespace(allow_private_urls=False)
    return value


def test_the_eu_data_centre_takes_the_code_and_password():
    allowed = creds().password_allowed
    assert allowed("successfactors", "https://career55.sapsf.eu/career?company=acme", JOB)
    assert not allowed("successfactors", "http://career55.sapsf.eu/career?company=acme", JOB)
    assert not allowed("successfactors", "https://career55.sapsf.eu.phish.test/career", JOB)


def test_the_eu_data_centre_is_a_tenant_scoped_host():
    assert sf_company("https://career55.sapsf.eu/career?company=acmeP") == ("https://career55.sapsf.eu", "acmeP")
    assert account_tenant("https://career55.sapsf.eu/career?company=acmeP", JOB) == "company:acmeP"


def test_its_mail_is_the_platforms():
    assert "sapsf.eu" in KNOWN_SENDERS["successfactors"]
