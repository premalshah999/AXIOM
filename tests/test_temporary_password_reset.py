"""Avature's reset answer (Electronic Arts, Lenovo, MetLife, Bain, Intuit, 2026-10-29): a "Password activation" mail that
carries a TEMPORARY PASSWORD and only a link to the sign-in page. The window waited for a reset link, every poll refused
the mail as token_format, and by the next run the login page said "Temporary password has expired". The password is
extracted as a credential from the employer's own mail, kept sealed, and the very next sign-in uses it; the forced
new-password page that follows takes the shared password. Synthetic data only."""

import base64
import time
from unittest.mock import AsyncMock

import httpx

from jobpilot.accounts.store import TEMPORARY_PASSWORD_SECONDS
from jobpilot.adapters.base import Step
from jobpilot.db import MailChallenge
from jobpilot.mail import RuleInput, extract_message
from jobpilot.mail.extraction import latest_employer_code, temporary_passwords
from support_mail import seed
from test_forced_password_reset import reset_page
from test_one_page_reset import SIGN_IN, SITE, adapter, control, field, page

RULE = {"sender_domains": ["acme.test", "avature.net"], "link_origins": ["https://jobs.acme.test"]}
HTML = (
    "<html><body><p>Hello Alex,</p><p>Your temporary password is: <b>{password}</b></p>"
    '<p>Sign in at <a href="https://jobs.acme.test/en_US/careers/Login">the careers site</a>. You will be asked to '
    "choose a new password.</p></body></html>"
)


def mail(body=HTML.format(password="Tmp4x9Qz7"), *, host="acme.avature.net", subject="Password activation",
         received=None, message_id="activation-1"):
    return {
        "id": message_id,
        "internalDate": str(int((received or time.time()) * 1000)),
        "payload": {
            "mimeType": "text/html",
            "headers": [
                {"name": "From", "value": f"Acme Careers <noreply@{host}>"},
                {"name": "To", "value": "Alex <alex@example.test>"},
                {"name": "Subject", "value": subject},
                {"name": "Authentication-Results", "value": f"mx.google.com; dkim=pass; dmarc=pass header.from={host}"},
            ],
            "body": {"data": base64.urlsafe_b64encode(body.encode()).decode().rstrip("=")},
        },
    }


def window(kind="password_reset", **extra):
    return {"recipient": "alex@example.test", "since": time.time() - 30, "expires": time.time() + 30, "kind": kind,
            "tenant_tokens": ["acme"], **extra}


def test_a_temporary_password_mail_is_a_password_not_a_link():
    diagnostics = {}
    assert extract_message(mail(), RULE, window(), diagnostics=diagnostics) == {"kind": "password", "value": "Tmp4x9Qz7"}
    assert "Tmp4x9Qz7" not in str(diagnostics)  # a diagnosis never carries the credential


def test_the_other_labels_of_a_mailed_password_are_read():
    for text in ("Your temporary password: Zq7!kd02", "Temp password - Zq7!kd02", "Your password is Zq7!kd02.",
                 "Your one-time password is Zq7!kd02", "Initial password = Zq7!kd02 (valid 24 hours)"):
        assert temporary_passwords(text) == {"Zq7!kd02"}, text


def test_prose_after_the_label_is_never_a_password():
    for text in ("Your temporary password has expired. Request a new one.",
                 "Your password is unchanged. Your temporary password expires tomorrow.",
                 "Your password is not shared with anyone", "Reset your password: https://jobs.acme.test/reset?t=x",
                 "Choose a new password to continue"):
        assert temporary_passwords(text) == set(), text


def test_only_this_employers_mail_gives_up_its_password():
    # The shared platform sender mails every tenant: another employer's link and tenant never qualify.
    foreign = mail(HTML.format(password="Tmp4x9Qz7").replace("jobs.acme.test", "jobs.other.test"), host="other.avature.net")
    diagnostics = {}
    assert extract_message(foreign, RULE, window(), diagnostics=diagnostics) is None
    assert diagnostics["rejection"] == "tenant"
    # No link at all: the sender's own tenant label has to name the employer.
    bare = mail("Your temporary password is Tmp4x9Qz7", subject="Password activation")
    assert extract_message(bare, RULE, window(), diagnostics={}) == {"kind": "password", "value": "Tmp4x9Qz7"}
    assert extract_message(bare, RULE, window(tenant_tokens=["lenovo"]), diagnostics={}) is None


def test_two_different_passwords_are_never_guessed_and_other_windows_are_unchanged():
    two = mail("Your temporary password is Tmp4x9Qz7. Old temporary password: Old5k2Lm9. https://jobs.acme.test/Login")
    diagnostics = {}
    assert extract_message(two, RULE, window(), diagnostics=diagnostics) is None
    assert diagnostics["rejection"] == "ambiguous_token"
    # A link window still reads links; a mailed password is only a reset's answer.
    assert extract_message(mail(), RULE, window("link")) == {"kind": "link", "value": "https://jobs.acme.test/en_US/careers/Login"}
    link = "https://jobs.acme.test/reset?token=fixture"
    assert extract_message(mail(f'<a href="{link}">Reset your password</a>', subject="Reset your password"), RULE,
                           window()) == {"kind": "link", "value": link}


def test_a_resent_password_replaces_the_earlier_one():
    now = time.time()
    older, newer = mail(received=now - 20, message_id="m1"), mail(HTML.format(password="New8b3Wp1"), received=now - 5, message_id="m2")
    matches = [("m1", {"kind": "password", "value": "Tmp4x9Qz7"}, older, None),
               ("m2", {"kind": "password", "value": "New8b3Wp1"}, newer, None)]
    assert latest_employer_code(matches, window()) == matches[1:]
    tied = [("m1", matches[0][1], newer, None), ("m2", matches[1][1], newer, None)]
    assert latest_employer_code(tied, window()) == tied


async def test_the_poll_hands_a_reset_window_its_temporary_password(service):
    site = "https://jobs.acme.test"
    box, run = seed(service, employer=site)
    rule = await service.mail.save_rule(RuleInput(mailbox_id=box["id"], employer_origin=site,
                                                  sender_domains=["acme.test", "avature.net"]))
    challenge = service.mail.begin(run["id"], rule, "alex@example.test", "password_reset")
    message = mail()

    def route(request):
        if request.url.path.endswith("/messages"):
            return httpx.Response(200, json={"messages": [{"id": message["id"]}]})
        return httpx.Response(200, json=message)

    service.mail.transport = httpx.MockTransport(route)
    await service.mail.poll_once()
    with service.db.session() as session:
        row = session.get(MailChallenge, challenge["id"])
        assert row.state == "matched", row.reason
        assert "Tmp4x9Qz7" not in row.payload  # sealed like every other mail secret
    assert service.mail.vault.open(row.payload) == {"kind": "password", "value": "Tmp4x9Qz7"}


async def test_the_temporary_password_is_the_next_sign_in_and_the_forced_page_replaces_it(service):
    value = adapter(service)
    url = SITE + "/login"
    value.id = "avature"
    value.accounts.mark(url, "reset_requested", {"email": "alex@example.test", "password": "Stale1pw!"})
    value.form.scan.return_value = SIGN_IN
    value.classify = lambda _: Step.SIGN_IN
    assert await value.use_temporary_password(url, "Tmp4x9Qz7") is None
    assert value.finished == ["verified"]
    record = value.accounts.record(url)
    assert "Tmp4x9Qz7" not in str(record)  # sealed in the record
    assert record["confirmed_at"]  # only an existing account is mailed one
    assert value.accounts.credentials(url)["password"] == "Tmp4x9Qz7"

    # Sign in: the employer answers with its forced new-password page; the temporary login is not saved as the account's.
    value.form.frames["pw"].url = url
    value.auth_button = lambda obs, mode: obs["controls"][1] if len(obs["controls"]) > 1 else obs["controls"][0]
    value.auth_outcome = AsyncMock(return_value=("new_password", ""))
    assert await value.authenticate(SIGN_IN, "sign_in") is None
    value.locators["pw"].fill.assert_awaited_with("Tmp4x9Qz7", timeout=4000)
    assert value.auth_outcome.await_args.kwargs == {"forced_ok": True}
    assert value.accounts.saved(url)["password"] == "Stale1pw!"

    # The forced page sets the account's own password (its history rule asks for a fresh one); the temporary is spent.
    value.accounts.store.generated = lambda length: "FreshPass9!abcd"
    obs = reset_page()
    value.form.frames["confirm"] = value.form.frames["pw"]
    value.form.scan.side_effect = [{**obs, "controls": [control("go", "Next")]}, SIGN_IN]
    value.auth_outcome = AsyncMock(return_value=("ok", ""))
    assert await value.on_new_password(obs) is None
    assert value.accounts.saved(url)["password"] == "FreshPass9!abcd"
    assert value.accounts.store.temporary(url) is None
    assert value.accounts.record(url)["state"] == "password_reset"
    assert value.accounts.credentials(url)["password"] == "FreshPass9!abcd"


async def test_an_expired_or_refused_temporary_password_is_not_tried_again(service):
    value = adapter(service)
    url = SITE + "/login"
    value.accounts.mark(url, "reset_requested", {"email": "alex@example.test", "password": "Stale1pw!"})
    assert value.accounts.store.set_temporary(url, {"email": "alex@example.test", "password": "Tmp4x9Qz7"})
    assert value.accounts.credentials(url)["password"] == "Tmp4x9Qz7"
    record = value.accounts.record(url)
    value.service.db.set_setting(value.accounts.store.key(url),
                                 {**record, "temporary_password_at": time.time() - TEMPORARY_PASSWORD_SECONDS - 5})
    assert value.accounts.store.temporary(url) is None
    assert value.accounts.credentials(url)["password"] == "Stale1pw!"


async def test_a_code_box_takes_the_one_time_password_as_its_code(service):
    from test_one_page_reset import CODE

    value = adapter(service)
    value.form.scan.return_value = CODE
    value.wait_email = AsyncMock(return_value={"kind": "password", "value": "523632"})
    value.use_temporary_password = AsyncMock()
    value.set_new_password = AsyncMock(return_value=None)
    await value.complete_password_reset(SITE + "/reset-password")
    value.use_temporary_password.assert_not_awaited()
    value.locators["code"].fill.assert_awaited_with("523632", timeout=4000)
