"""Employer account records and imported browser sessions."""

import hashlib
import json
from urllib.parse import urlsplit

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from ...db import Setting
from ...network import public_url


def router(service) -> APIRouter:
    db, config = service.db, service.config
    r = APIRouter()

    @r.get("/api/accounts")
    def accounts():
        from ...accounts import summary
        from ...config import password_problems

        result = summary(service)
        result["password_problems"] = (
            password_problems(config.application_password) if config.application_password else []
        )
        return result

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
