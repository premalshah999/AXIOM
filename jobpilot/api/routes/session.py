"""Sign-in with the access token, and sign-out."""

import hmac
import time
from collections import defaultdict

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field


class Login(BaseModel):
    token: str = Field(max_length=256)


def router(service, signer) -> APIRouter:
    config = service.config
    attempts = defaultdict(list)
    r = APIRouter()

    @r.post("/api/login")
    async def login(data: Login, request: Request):
        addr = request.client.host if request.client else "local"
        attempts[addr] = [t for t in attempts[addr] if t > time.monotonic() - 60]
        if len(attempts[addr]) >= 10:
            raise HTTPException(429, "Too many attempts. Try again in a minute.")
        attempts[addr].append(time.monotonic())
        if not hmac.compare_digest(data.token, config.app_token):
            raise HTTPException(401, "Incorrect access token")
        response = JSONResponse({"ok": True})
        response.set_cookie(
            "jp_session",
            signer.dumps("applicant"),
            httponly=True,
            secure=config.secure_cookies,
            samesite="strict",
            max_age=86400,
        )
        return response

    @r.post("/api/logout")
    async def logout():
        response = JSONResponse({"ok": True})
        response.delete_cookie("jp_session")
        return response

    return r
