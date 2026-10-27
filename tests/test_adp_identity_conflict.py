"""ADP (ZincFive, Avalon 2026-10-27): sign-in answers Person Not Found (4040) and registration "another user account
already exists" (40014), offering only "an alternate mobile number or an email address"; a Gmail variant of the address
is the same address to ADP. The owner wants ADP fixed, not closed: a dotted Gmail alias is the distinct address."""

from types import SimpleNamespace

from jobpilot.browser import BrowserEngine


async def test_an_adp_identity_conflict_waits_for_recovery():
    e = object.__new__(BrowserEngine)
    e.job = {"ats": "adp", "url": "https://workforcenow.adp.com/mascsr/default/mdf/recruitment/recruitment.html"}
    e.form = SimpleNamespace(network=SimpleNamespace(account_conflict=lambda: True))
    e.result = None
    assert await e.handle_account() is True
    assert e.result["state"] == "waiting_browser" and "40014" in e.result["reason"]


def test_a_dotted_alias_reaches_the_same_inbox_but_is_a_new_address():
    from jobpilot.email_alias import ats_address, same_mailbox

    alias = ats_address("alexdoe.mail@gmail.com", "adp", {"adp": ".4"})
    assert alias == "alex.doe.mail@gmail.com"
    assert same_mailbox(alias, "alexdoe.mail@gmail.com") and alias != "alexdoe.mail@gmail.com"
    assert ats_address("alexdoe.mail@gmail.com", "icims", {"adp": ".4"}) == "alexdoe.mail@gmail.com"
