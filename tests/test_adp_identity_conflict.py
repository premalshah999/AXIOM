"""ADP (ZincFive, Avalon 2026-10-27): sign-in answers Person Not Found (4040) and registration "another user account
already exists" (40014), offering only "an alternate mobile number or an email address"; a Gmail variant of the address
is the same address to ADP. A phone code is closed under the owner's rule, so the application closes."""

from types import SimpleNamespace

from jobpilot.browser import BrowserEngine


async def test_an_adp_identity_conflict_closes_the_application():
    e = object.__new__(BrowserEngine)
    e.job = {"ats": "adp", "url": "https://workforcenow.adp.com/mascsr/default/mdf/recruitment/recruitment.html"}
    e.form = SimpleNamespace(network=SimpleNamespace(account_conflict=lambda: True))
    e.result = None
    assert await e.handle_account() is True
    assert e.result["state"] == "closed" and "mobile-number sign-in" in e.result["reason"]
