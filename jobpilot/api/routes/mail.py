import asyncio

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from ...db import MailChallenge, MailRule
from ...mail import LIVE, RuleInput


class ImapConnect(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    app_password: str = Field(min_length=1, max_length=64)


def router(service) -> APIRouter:
    mail, config = service.mail, service.config
    r = APIRouter()

    @r.get("/api/mail")
    def status():
        return mail.summary()

    @r.get("/api/replies")
    def replies():
        """How many applications an employer has answered, read from the connected mailbox (jobpilot/replies.py)."""
        from ...replies import summary

        return summary(service.db)

    @r.post("/api/mail/connect")
    async def connect():
        url, binding = mail.begin_oauth()
        response = JSONResponse({"authorization_url": url})
        response.set_cookie(
            "jp_mail_oauth",
            binding,
            max_age=600,
            httponly=True,
            secure=config.secure_cookies,
            samesite="lax",
            path="/oauth/gmail/callback",
        )
        return response

    @r.get("/oauth/gmail/callback")
    async def callback(request: Request):
        try:
            await mail.finish_oauth(
                request.query_params.get("state", ""),
                request.cookies.get("jp_mail_oauth", ""),
                request.query_params.get("code", ""),
            )
            result = "connected"
        except (ValueError, httpx.HTTPError, KeyError, TypeError):
            result = "connection_failed"
        response = RedirectResponse(config.base_url.rstrip("/") + "/?gmail=" + result, status_code=303)
        response.delete_cookie("jp_mail_oauth", path="/oauth/gmail/callback")
        response.headers["Cache-Control"] = "no-store"
        return response

    @r.post("/api/mail/imap")
    async def connect_imap(data: ImapConnect):
        """Gmail with an app password, for installations without Google sign-in configured."""
        return await mail.connect_imap(data.email, data.app_password)

    @r.post("/api/mail/{mailbox_id}/check")
    async def check(mailbox_id: str):
        profile = await mail.request(mailbox_id, "/profile")
        return {"connected": True, "email": profile.get("emailAddress", "")}

    @r.delete("/api/mail/{mailbox_id}")
    async def disconnect(mailbox_id: str):
        return await mail.disconnect(mailbox_id)

    @r.post("/api/mail-rules")
    async def rule(data: RuleInput):
        return await mail.save_rule(data)

    @r.delete("/api/mail-rules/{rule_id}")
    def remove_rule(rule_id: str):
        with service.db.exclusive() as s:
            row = s.get(MailRule, rule_id)
            if not row:
                raise ValueError("Rule not found")
            s.delete(row)
            for challenge in s.scalars(
                select(MailChallenge).where(MailChallenge.rule_id == rule_id, MailChallenge.state.in_(LIVE))
            ):
                challenge.state, challenge.payload, challenge.reason = (
                    "cancelled",
                    "",
                    "Employer rule removed",
                )
        return {"removed": True}

    @r.post("/api/mail-challenges/{challenge_id}/cancel")
    def cancel(challenge_id: str):
        with service.db.session() as s:
            row = s.get(MailChallenge, challenge_id)
            if not row or row.state not in LIVE:
                raise ValueError("No active challenge with this ID")
        mail.outcome(challenge_id, "cancelled", "Cancelled by applicant")
        return {"cancelled": True}

    @r.post("/api/inbox/check")
    async def inbox_check():
        from ...gmail_imap import request

        try:
            if not (config.gmail_address and config.gmail_app_password):
                # A mailbox connected in the app has its own password: this button said "Set GMAIL_ADDRESS in .env" to all of them.
                box = service.inbox.mailbox()
                if not box:
                    raise ValueError("Connect your Gmail in Settings > Connections first")
                await mail.request(box, "/profile")
                return {"connected": True, "folder": "INBOX", "host": "imap.gmail.com"}
            await asyncio.to_thread(
                request,
                config.gmail_address.strip().lower(),
                "".join(config.gmail_app_password.split()),
                "/profile",
            )
        except ValueError:
            raise
        except Exception:
            # imaplib errors carry the server's reply, never the password.
            raise ValueError(
                "Gmail didn't accept the login. Check the app password and that IMAP is on."
            ) from None
        return {"connected": True, "folder": "INBOX", "host": "imap.gmail.com"}

    return r
