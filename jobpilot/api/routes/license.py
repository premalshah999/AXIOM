"""The license screen's two routes (jobpilot/licensing.py). Both are public: the first thing a Mac shows when a build asks for a key
is its code and a place to paste the key, before there is any account to sign in to. Neither does anything but read this Mac's
code and check a pasted key; a key that is not genuine, not for this Mac, or past its date is refused and changes nothing."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ...licensing import LicenseError, licensing


class KeyIn(BaseModel):
    key: str = Field(max_length=2000)


def router(service) -> APIRouter:
    folder = service.config.data_dir
    r = APIRouter()

    @r.get("/api/license")
    def read():
        return licensing(folder).status()

    @r.post("/api/license/key")
    def enter(data: KeyIn):
        try:
            return licensing(folder).install(data.key)
        except LicenseError as exc:
            raise HTTPException(400, str(exc)) from None

    return r
