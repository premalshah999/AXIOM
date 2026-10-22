"""Sign-in with the access token, and sign-out."""

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ..sessions import same_secret


class Login(BaseModel):
    token: str = Field(max_length=256)
    keep: bool = False


def router(service, sessions, throttle) -> APIRouter:
    config = service.config
    r = APIRouter()

    @r.post("/api/login")
    def login(data: Login, request: Request):
        throttle.check(request)
        if not same_secret(data.token.strip(), config.app_token):
            raise HTTPException(401, "Incorrect access token" + throttle.fail(request))
        response = JSONResponse({"ok": True})
        sessions.issue(response, keep=data.keep)
        return response

    @r.post("/api/logout")
    def logout():
        """Public, so a browser whose session already ended can still clear its cookie."""
        response = JSONResponse({"ok": True})
        sessions.clear(response)
        return response

    return r
