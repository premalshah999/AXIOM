"""Gmail with an app password entered in Setup (2026-10-15): verified before saving, sealed in the
vault, never returned, and used for that mailbox instead of GMAIL_APP_PASSWORD in .env."""

import asyncio

from jobpilot import gmail_imap
from jobpilot.db import Mailbox
from jobpilot.mail_errors import MailAuthenticationError

APP_PASSWORD = "abcd efgh ijkl mnop"


def fake_gmail(monkeypatch, accept="abcdefghijklmnop"):
    calls = []

    def request(address, password, path, params=None):
        calls.append((address, password, path))
        if password != accept:
            raise MailAuthenticationError("Gmail authentication failed; check the saved app password")
        return {"emailAddress": address}

    monkeypatch.setattr(gmail_imap, "request", request)
    return calls


def test_app_password_is_verified_sealed_and_used(client, service, monkeypatch):
    calls = fake_gmail(monkeypatch)
    r = client.post("/api/mail/imap", json={"email": "Ada@Example.com", "app_password": APP_PASSWORD})
    assert r.status_code == 200, r.text
    box = r.json()
    assert (
        box["email"] == "ada@example.com" and box["provider"] == "gmail_imap" and box["state"] == "connected"
    )
    assert "credentials" not in box and "app_password" not in r.text
    # Spaces from Google's display are removed before the login.
    assert calls[-1] == ("ada@example.com", "abcdefghijklmnop", "/profile")
    with service.db.session() as s:
        stored = s.get(Mailbox, box["id"]).credentials
    assert "abcdefghijklmnop" not in stored  # sealed, not plain text
    summary = client.get("/api/mail").json()
    assert (
        summary["mailboxes"][0]["provider"] == "gmail_imap" and "credentials" not in summary["mailboxes"][0]
    )
    # Reading mail for this mailbox uses its own password (GMAIL_APP_PASSWORD is empty in tests).
    assert asyncio.run(service.mail.request(box["id"], "/profile")) == {"emailAddress": "ada@example.com"}
    assert client.post(f"/api/mail/{box['id']}/check", json={}).json()["connected"] is True


def test_wrong_or_malformed_app_password_saves_nothing(client, service, monkeypatch):
    fake_gmail(monkeypatch)
    rejected = client.post(
        "/api/mail/imap", json={"email": "ada@example.com", "app_password": "zzzz zzzz zzzz zzzz"}
    )
    assert rejected.status_code == 400 and "rejected" in rejected.json()["detail"]
    normal = client.post(
        "/api/mail/imap", json={"email": "ada@example.com", "app_password": "MyNormalPassword1!"}
    )
    assert normal.status_code == 400 and "16 letters" in normal.json()["detail"]
    assert client.get("/api/mail").json()["mailboxes"] == []


def test_disconnecting_an_app_password_mailbox(client, monkeypatch):
    fake_gmail(monkeypatch)
    box = client.post(
        "/api/mail/imap", json={"email": "ada@example.com", "app_password": APP_PASSWORD}
    ).json()
    r = client.delete(f"/api/mail/{box['id']}").json()
    assert r["disconnected"] is True and "Google account" in r["message"] and ".env" not in r["message"]
    assert client.get("/api/mail").json()["mailboxes"] == []
