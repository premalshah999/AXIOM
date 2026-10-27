"""Amazon (2026-10-27): amazon.jobs signs candidates in on atozpublic.idprism-auth.amazon.com, and its password reset
code was refused as "an unexpected website". The employer's own name under another domain is its site."""

from types import SimpleNamespace

from jobpilot.accounts.credentials import Accounts


def creds():
    value = object.__new__(Accounts)
    value.config = SimpleNamespace(allow_private_urls=False)
    return value


def test_the_employers_name_under_another_domain_takes_the_code():
    allowed = creds().password_allowed
    assert allowed("custom", "https://atozpublic.idprism-auth.amazon.com/", "https://www.amazon.jobs/jobs/1/apply")
    assert not allowed("custom", "https://login.example-phish.com/", "https://www.amazon.jobs/jobs/1/apply")
    assert not allowed("custom", "http://atozpublic.idprism-auth.amazon.com/", "https://www.amazon.jobs/jobs/1/apply")
