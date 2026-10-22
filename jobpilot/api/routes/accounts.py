"""Employer account records (Setup → Accounts) and imported browser sessions. No response carries a password."""

import hashlib
import json
from urllib.parse import urlsplit

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from ...admission import QueueBlocked
from ...db import Setting
from ...network import public_url


class EmployerPassword(BaseModel):
    """The password the owner set on the employer's own site. Stored sealed; never echoed."""

    password: str = Field(default="", max_length=200)


def router(service) -> APIRouter:
    db, config = service.db, service.config
    r = APIRouter()

    @r.get("/api/accounts")
    def accounts():
        from ...accounts.overview import overview
        from ...config import password_problems

        return {
            "accounts": overview(service),
            "password_problems": (
                password_problems(config.application_password) if config.application_password else []
            ),
        }

    @r.put("/api/accounts/{account_id}/password")
    def account_password(account_id: str, data: EmployerPassword):
        """ "I reset it myself": the next application signs in with this password instead of asking for a reset."""
        from ...accounts.overview import one
        from ...accounts.store import save_owner_password

        if not data.password.strip():
            raise ValueError("Enter the new password you set on the employer's site")
        if not save_owner_password(service, account_id, data.password):
            raise HTTPException(404, "This account record no longer exists")
        return {"ok": True, "account": one(service, account_id)}

    @r.post("/api/accounts/{account_id}/verify")
    async def account_verify(account_id: str):
        """Restart the paused application that is waiting on this employer's verification email."""
        from ...accounts.overview import draft_for

        record = db.get_setting(account_id) if account_id.startswith("employer_account:") else None
        if not record:
            raise HTTPException(404, "This account record no longer exists")
        if record.get("state") != "verification_pending":
            raise HTTPException(409, "Only an account waiting for email verification can retry it")
        draft = draft_for(service, account_id)
        if not draft:
            raise HTTPException(
                409,
                "No paused application is waiting on this employer's verification email. The next application "
                "to this employer signs in and asks for a new one.",
            )
        run_id, challenge_id = draft
        try:
            result = await service.resume_mail_wait(run_id, challenge_id, restart_verification=True)
        except QueueBlocked as blocked:
            raise HTTPException(409, str(blocked)) from None
        except ValueError:
            result = None
        if not result:
            raise HTTPException(
                409,
                "The paused application can no longer retry verification. The next application to this "
                "employer signs in and asks for a new email.",
            )
        return {"ok": True, "run_id": result.get("run_id", run_id), "state": result.get("state", "queued")}

    @r.delete("/api/accounts/{account_id}")
    async def account_forget(account_id: str):
        # Forgetting a record makes the next run sign in again or create the account; the
        # shared login itself is kept.
        if not account_id.startswith("employer_account:"):
            raise HTTPException(404)
        with db.exclusive() as s:
            row = s.get(Setting, account_id)
            if not row:
                raise HTTPException(404)
            s.delete(row)
        return {"deleted": True}

    @r.post("/api/sessions")
    async def session_import(url: str = Form(...), file: UploadFile = File(...)):
        await public_url(url, config.allow_private_urls)
        host = urlsplit(url).hostname
        content = await file.read(2 * 1024 * 1024 + 1)
        if len(content) > 2 * 1024 * 1024:
            raise ValueError("Session state exceeds 2 MB")
        try:
            state = json.loads(content)
            for cookie in state.get("cookies", []):
                domain = cookie.get("domain", "").lstrip(".")
                # Import only cookies belonging to this exact employer host or its suffix.
                if not domain or not (host == domain or host.endswith("." + domain)) or domain.count(".") < 1:
                    raise ValueError("Session contains unrelated cookies")
            for origin in state.get("origins", []):
                if urlsplit(origin["origin"]).hostname != host:
                    raise ValueError("Session contains unrelated origins")
        except (TypeError, KeyError, json.JSONDecodeError):
            raise ValueError("Upload a Playwright storage-state JSON file") from None
        path = config.data_dir / "sessions" / (hashlib.sha256(host.encode()).hexdigest() + ".json")
        path.write_text(json.dumps(state))
        path.chmod(0o600)
        return {"saved": True, "host": host}

    return r
