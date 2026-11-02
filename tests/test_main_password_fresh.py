"""The password flow on a new install, through the real engine, a real Chrome and a real mailbox socket (2026-11-01).

The owner: the main password is tried first; an employer that refuses it gets a random one that meets its rules, saved for that
employer alone; a sign-in tries the saved password, then the main one; a reset sets the main password. tests/test_main_password.py
proves each step on the generic adapter with a fake mailbox. Here the employer's own record says which password it was sent, the main
password is set through the route the Settings screen and the setup guide call, and Workday's own account controller, which has its
own code path, is held to the same rules as the generic one. Synthetic data only."""

import pytest

from jobpilot.accounts import AccountStore
from jobpilot.accounts.overview import password_kind
from mail_formats import ME, PLATFORMS
from test_mail_fresh_run import imap, one_run  # noqa: F401  (imap is a fixture)

pytestmark = [pytest.mark.browser, pytest.mark.integration]
MAIN = "Mainpass-2026!x"
PLATFORM_CASES = ["custom", "workday"]


def set_main(client, service, password=MAIN):
    r = client.put("/api/connections", json={"values": {"application_password": password}})
    assert r.status_code == 200, r.text
    assert service.config.application_password == password


def saved(service, platform):
    url = PLATFORMS[platform]["url"]
    store = AccountStore(service, ME, url)
    record = store.record(url)
    return (service.mail.vault.open(record["credentials"])["password"] if record.get("credentials") else None), password_kind(service, record)


def why(site, outcome, events):
    return (site.created, site.signins, site.new_passwords, site.posts, site.sent, outcome, [m for k, m, _ in events if k in {"account", "auth", "run_failed"}][-14:])


@pytest.mark.parametrize("platform", PLATFORM_CASES)
async def test_a_new_account_gets_the_main_password(client, service, imap, platform):
    set_main(client, service)
    site, outcome, events, _, _ = await one_run(client, service, imap, platform, "link")
    assert site.created == [MAIN], why(site, outcome, events)
    assert "link" in site.used, why(site, outcome, events)
    assert saved(service, platform) == (MAIN, "main")


@pytest.mark.parametrize("platform", PLATFORM_CASES)
async def test_an_employer_that_refuses_it_gets_a_random_one_saved_for_it_alone(client, service, imap, platform):
    set_main(client, service)
    site, outcome, events, _, _ = await one_run(client, service, imap, platform, "link", site_options={"refuse": {MAIN}})
    assert site.created[:1] == [MAIN] and len(site.created) == 2, why(site, outcome, events)
    random_one = site.created[1]
    assert random_one != MAIN and "link" in site.used, why(site, outcome, events)
    assert saved(service, platform) == (random_one, "own")
    assert MAIN not in str(events) and random_one not in str(events)


@pytest.mark.parametrize("platform", PLATFORM_CASES)
async def test_a_reset_sets_the_main_password(client, service, imap, platform):
    set_main(client, service)
    site, outcome, events, _, _ = await one_run(client, service, imap, platform, "reset")
    assert site.new_passwords == [MAIN], why(site, outcome, events)
    assert saved(service, platform) == (MAIN, "main")


@pytest.mark.parametrize("platform", PLATFORM_CASES)
async def test_a_reset_refused_for_the_main_password_sets_a_random_one(client, service, imap, platform):
    set_main(client, service)
    site, outcome, events, _, _ = await one_run(client, service, imap, platform, "reset", site_options={"refuse": {MAIN}})
    assert site.new_passwords[:1] == [MAIN] and len(site.new_passwords) == 2, why(site, outcome, events)
    assert site.password == site.new_passwords[1] != MAIN, why(site, outcome, events)
    assert saved(service, platform) == (site.password, "own")


@pytest.mark.parametrize("platform", PLATFORM_CASES)
async def test_a_sign_in_tries_the_saved_password_then_the_main_one_before_any_reset(client, service, imap, platform):
    """The employer holds the main password; this install's record holds an older one."""
    url = PLATFORMS[platform]["url"]
    set_main(client, service)

    def older_record(site):
        AccountStore(service, ME, url).save(url, {"email": ME, "password": "Older-saved-77!x"}, "authenticated")

    site, outcome, events, _, _ = await one_run(
        client, service, imap, platform, "reset", site_options={"password": MAIN}, before=older_record
    )
    assert site.signins[:2] == ["Older-saved-77!x", MAIN], why(site, outcome, events)
    assert "/password/forgot" not in site.posts and not site.new_passwords, why(site, outcome, events)
    assert saved(service, platform) == (MAIN, "main")
