"""Workday sign-up without its activation mail: the retry must not repeat the same Create (2026-10-29/31), and a page still
rendering must not leave one letter of the email in the box (2026-10-31)."""

from types import SimpleNamespace

import pytest

from jobpilot.mail_errors import MailPending
from jobpilot.workday import WorkdayAuth

URL = "https://example.wd1.myworkdayjobs.com/en-US/careers/job/Austin/Engineer_R-1"


def auth_for(service, mail_wait):
    async def wait(cid, *, max_wait_seconds=None):
        return await mail_wait(cid)

    mail = SimpleNamespace(wait=wait, vault=service.mail.vault, outcome=lambda *a, **k: None, begin=lambda *a, **k: {"id": "c2"})
    engine = SimpleNamespace(
        job={"url": URL}, page=None, db=service.db, service=SimpleNamespace(mail=mail),
        form=SimpleNamespace(resolver=SimpleNamespace(profile=SimpleNamespace(email="alex@example.test"))),
        email_verification=SimpleNamespace(rule={"link_origins": []}, challenge_id="c1", deferred=False),
        run_id="r", emit=lambda *a, **k: None, verification_origins=None, config=SimpleNamespace(allow_private_urls=False),
    )
    auth = WorkdayAuth(engine)
    auth.shared_credentials = lambda: {"email": "alex@example.test", "password": "synthetic-password"}
    return auth


async def test_an_activation_mail_still_out_leaves_the_account_known_for_the_next_try(service):
    async def pending(cid):
        raise MailPending({"id": cid})

    auth = auth_for(service, pending)
    assert auth.credentials(create=True)
    assert auth.account_state == "created_locally"
    with pytest.raises(MailPending):
        await auth.activate()
    # A new run: its own WorkdayAuth reads the saved state, signs in, and sign-in's "not verified" leads to Resend.
    again = auth_for(service, pending)
    assert again.credentials() and again.account_state == "verification_pending"


async def test_a_field_taken_away_by_a_re_render_is_typed_again(page):
    await page.set_content("<div id=box><input id=email></div>")
    # The first keystroke replaces the input, as a page still rendering does; the second attempt sticks.
    await page.evaluate(
        """() => { let done = false; document.addEventListener('input', () => {
             if (done) return; done = true;
             document.getElementById('box').innerHTML = '<input id=email>';
           }, true); }"""
    )
    await WorkdayAuth(None).enter(page.locator("#email"), "alex@example.test")
    assert await page.locator("#email").input_value() == "alex@example.test"
