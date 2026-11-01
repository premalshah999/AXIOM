"""2026-10-22: one refusal of Gmail's LOGIN while the Mac woke on battery was read as a bad app password; the mailbox
was marked reconnect_required and every email verification failed for five hours, though the password was fine."""

import imaplib
from datetime import UTC, datetime, timedelta

import pytest

from jobpilot import gmail_imap
from jobpilot.db import Mailbox
from jobpilot.mail_errors import MailAuthenticationError, MailTransportError


class FakeIMAP:
    refusal = ""

    def __init__(self, *args, **kwargs):
        pass

    def login(self, address, password):
        if self.refusal:
            raise imaplib.IMAP4.error(self.refusal)
        return "OK", [b"ok"]

    def logout(self):
        pass


@pytest.mark.parametrize(
    "refusal,error",
    [
        ("b'[AUTHENTICATIONFAILED] Invalid credentials (Failure)'", MailAuthenticationError),
        ("b'[ALERT] Application-specific password required'", MailAuthenticationError),
        ("b'[ALERT] Too many simultaneous connections. (Failure)'", MailTransportError),
        ("b'[UNAVAILABLE] Temporary System Problem. Try again later'", MailTransportError),
    ],
)
def test_only_rejected_credentials_count_as_a_bad_password(monkeypatch, refusal, error):
    monkeypatch.setattr(gmail_imap.imaplib, "IMAP4_SSL", type("F", (FakeIMAP,), {"refusal": refusal}))
    with pytest.raises(error):
        gmail_imap._connect("alex@example.test", "abcdabcdabcdabcd")


@pytest.mark.parametrize("accepted,state", [(True, "connected"), (False, "reconnect_required")])
async def test_a_mailbox_marked_for_reconnect_heals_when_gmail_accepts_again(service, monkeypatch, accepted, state):
    service.config.gmail_address, service.config.gmail_app_password = "alex@example.test", "abcd abcd abcd abcd"
    stale = (datetime.now(UTC) - timedelta(minutes=10)).isoformat()
    with service.db.exclusive() as s:
        s.add(Mailbox(id="box", email="alex@example.test", credentials="sealed", state="reconnect_required",
                      error="Gmail login failed", checked_at=stale))
    monkeypatch.setattr(gmail_imap, "login_ok", lambda address, password: accepted)
    assert await service.mail.heal_mailbox() is accepted
    with service.db.session() as s:
        assert s.get(Mailbox, "box").state == state
    # Checked at most every five minutes.
    monkeypatch.setattr(gmail_imap, "login_ok", lambda address, password: pytest.fail("checked again too soon"))
    assert await service.mail.heal_mailbox() is False


async def test_a_mailbox_connected_in_the_app_heals_like_the_one_in_env(service, monkeypatch):
    from jobpilot import gmail_imap
    from jobpilot.db import Mailbox

    mail = service.mail
    with service.db.exclusive() as s:
        s.add(Mailbox(email="alex@gmail.example", state="reconnect_required", error="Gmail login failed",
                      credentials=mail.vault.seal({"provider": "gmail_imap", "address": "alex@gmail.example", "app_password": "abcd efgh ijkl mnop"})))
        s.add(Mailbox(email="oauth@gmail.example", state="reconnect_required", error="token",
                      credentials=mail.vault.seal({"provider": "gmail_oauth", "refresh_token": "x"})))
    tried = []
    monkeypatch.setattr(gmail_imap, "login_ok", lambda address, password: tried.append((address, password)) or True)
    assert await mail.heal_mailbox() is True
    assert tried == [("alex@gmail.example", "abcdefghijklmnop")]  # A Google sign-in has no password to try.
    with service.db.session() as s:
        states = {b.email: b.state for b in s.scalars(__import__("sqlalchemy").select(Mailbox))}
    assert states == {"alex@gmail.example": "connected", "oauth@gmail.example": "reconnect_required"}
