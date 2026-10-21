"""Connecting, listing and disconnecting mailboxes (Google OAuth and Gmail app passwords)."""

from sqlalchemy import select
from urllib.parse import urlencode
import asyncio
import base64
import hashlib
import hmac
import httpx
import re
import secrets
import time
from ..db import MailChallenge
from ..db import MailOAuth
from ..db import MailRule
from ..db import Mailbox
from ..db import record
from ..mail_errors import MailAuthenticationError
from .constants import API_URL
from .constants import LIVE
from .constants import SCOPE
from .constants import TOKEN_URL
from .helpers import digest


class MailboxesMixin:
    def client(self):
        return httpx.AsyncClient(
            transport=self.transport, timeout=12, follow_redirects=False, trust_env=False
        )

    @property
    def callback_url(self):
        return self.config.base_url.rstrip("/") + "/oauth/gmail/callback"

    def summary(self):
        with self.db.session() as s:
            boxes = [
                {k: v for k, v in record(m).items() if k != "credentials"} | {"provider": self.provider(m)}
                for m in s.scalars(select(Mailbox))
            ]
            rules = [record(r) for r in s.scalars(select(MailRule))]
            challenges = [
                {k: v for k, v in record(c).items() if k not in {"payload", "message_id"}}
                for c in s.scalars(select(MailChallenge).order_by(MailChallenge.created_at.desc()).limit(50))
            ]
        return {
            "configured": bool(self.config.google_client_id and self.config.google_client_secret),
            "redirect_uri": self.callback_url,
            "mailboxes": boxes,
            "rules": rules,
            "challenges": challenges,
        }

    def begin_oauth(self):
        if not self.config.google_client_id or not self.config.google_client_secret:
            raise ValueError("Configure GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET on the server first")
        state, browser, verifier = (secrets.token_urlsafe(32) for _ in range(3))
        with self.db.exclusive() as s:
            for old in s.scalars(select(MailOAuth).where(MailOAuth.expires < time.time())):
                s.delete(old)
            s.add(
                MailOAuth(
                    state_hash=digest(state),
                    browser_hash=digest(browser),
                    verifier=self.vault.seal(verifier),
                    expires=time.time() + 600,
                )
            )
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        return "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(
            {
                "client_id": self.config.google_client_id,
                "redirect_uri": self.callback_url,
                "response_type": "code",
                "scope": SCOPE,
                "access_type": "offline",
                "prompt": "consent select_account",
                "state": state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
        ), browser

    async def finish_oauth(self, state, browser, code):
        with self.db.exclusive() as s:
            pending = s.get(MailOAuth, digest(state))
            if (
                not pending
                or pending.expires < time.time()
                or not browser
                or not hmac.compare_digest(pending.browser_hash, digest(browser))
            ):
                raise ValueError("Gmail authorization expired or belongs to another browser. Start again.")
            verifier = self.vault.open(pending.verifier)
            s.delete(pending)  # Single-use even when token exchange fails.
        async with self.client() as client:
            r = await client.post(
                TOKEN_URL,
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "client_id": self.config.google_client_id,
                    "client_secret": self.config.google_client_secret,
                    "redirect_uri": self.callback_url,
                    "code_verifier": verifier,
                },
            )
            if r.status_code != 200:
                raise ValueError("Google could not complete authorization. Reconnect Gmail.")
            token = r.json()
            if (
                SCOPE not in token.get("scope", "").split()
                or not token.get("access_token")
                or not token.get("refresh_token")
            ):
                raise ValueError("Read-only Gmail access and offline consent are required. Reconnect Gmail.")
            p = await client.get(
                API_URL + "/profile", headers={"Authorization": "Bearer " + token["access_token"]}
            )
            if p.status_code != 200:
                raise ValueError("Gmail API is unavailable. Enable it in your Google Cloud project.")
            email = p.json()["emailAddress"].lower()
        token["expires_at"] = time.time() + token.get("expires_in", 3600)
        with self.db.exclusive() as s:
            box = s.scalar(select(Mailbox).where(Mailbox.email == email))
            if box:
                box.credentials, box.state, box.error = self.vault.seal(token), "connected", ""
            else:
                s.add(Mailbox(email=email, credentials=self.vault.seal(token)))
        return email

    def provider(self, box):
        """'gmail_imap' (app password) or 'gmail_oauth' (Google sign-in); never the credentials."""
        try:
            return self.vault.open(box.credentials).get("provider") or "gmail_oauth"
        except Exception:
            return "unknown"

    async def connect_imap(self, address, app_password):
        """Connect a Gmail inbox with an app password entered in the app.

        The login is verified before anything is saved; the password is sealed in the vault like
        OAuth tokens and is never returned. GMAIL_ADDRESS/GMAIL_APP_PASSWORD in .env keep working."""
        from ..gmail_imap import request

        email = address.strip().lower()
        password = "".join(app_password.split())
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            raise ValueError("Enter the Gmail address you apply with")
        if not re.fullmatch(r"[A-Za-z]{16}", password):
            raise ValueError(
                "A Google app password is 16 letters (spaces are fine). Your normal password won't work."
            )
        try:
            await asyncio.to_thread(request, email, password, "/profile")
        except MailAuthenticationError:
            raise ValueError(
                "Gmail rejected this app password. Check it, and that 2-Step Verification and IMAP are on."
            ) from None
        except Exception as exc:
            raise ValueError(
                f"Could not reach Gmail ({type(exc).__name__}). Try again in a minute."
            ) from None
        sealed = self.vault.seal({"provider": "gmail_imap", "address": email, "app_password": password})
        with self.db.exclusive() as s:
            box = s.scalar(select(Mailbox).where(Mailbox.email == email))
            if box:
                box.credentials, box.state, box.error = sealed, "connected", ""
            else:
                box = Mailbox(email=email, credentials=sealed)
                s.add(box)
            s.flush()
            return {k: v for k, v in record(box).items() if k != "credentials"} | {"provider": "gmail_imap"}

    def connection_error(self, mailbox_id, state, error):
        with self.db.exclusive() as s:
            if box := s.get(Mailbox, mailbox_id):
                box.state, box.error = state, error

    async def disconnect(self, mailbox_id):
        async with self.locks.setdefault(mailbox_id, asyncio.Lock()):
            with self.db.exclusive() as s:
                box = s.get(Mailbox, mailbox_id)
                if not box:
                    raise ValueError("Mailbox not found")
                token = self.vault.open(box.credentials)
                s.delete(box)
                for rule in s.scalars(select(MailRule).where(MailRule.mailbox_id == mailbox_id)):
                    s.delete(rule)
                for c in s.scalars(
                    select(MailChallenge).where(
                        MailChallenge.mailbox_id == mailbox_id, MailChallenge.state.in_(LIVE)
                    )
                ):
                    c.state, c.payload, c.reason = "cancelled", "", "Mailbox disconnected"
            revoked = False
            if token.get("provider") == "gmail_imap":
                return {
                    "disconnected": True,
                    "revoked": False,
                    "message": "Disconnected. Delete the app password in your Google account if you no longer use it."
                    if token.get("app_password")
                    else "Remove GMAIL_APP_PASSWORD from .env and revoke it in your Google account to keep it disconnected.",
                }
            try:
                async with self.client() as client:
                    r = await client.post(
                        "https://oauth2.googleapis.com/revoke", data={"token": token["refresh_token"]}
                    )
                    revoked = r.status_code == 200
            except httpx.HTTPError:
                pass
            return {"disconnected": True, "revoked": revoked}
