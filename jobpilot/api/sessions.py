"""Browser sessions (the signed `jp_session` cookie) and the one sign-in throttle every credential check shares.

A cookie carries the owner subject, the session version it was signed under and whether the owner asked to stay
signed in. Use renews it (sliding expiry), so a dashboard left open never expires in the middle of work: an
ordinary session ends after 24 hours without use, a kept one after 30 days. Raising the session version
(password change, reset, "Sign out everywhere", `jobpilot reset-password`) ends every cookie signed before it.

Cookies signed before 2026-10-22 hold only the constant subject. They keep working for their 24 hours until the
first version change, and are re-signed in the new form on their next use.
"""

import hmac
import math
import time
from collections import defaultdict

from fastapi import HTTPException, Request
from itsdangerous import BadSignature, SignatureExpired

from ..owner_account import SESSION_SUBJECT, session_version

COOKIE = "jp_session"
SESSION_SECONDS = 86400
KEEP_SECONDS = 30 * 86400
# Re-sign a used cookie at most every ten minutes: the dashboard polls every few seconds.
RENEW_AFTER = 600
# A version read from the database is trusted this long. `jobpilot reset-password` writes it from another
# process, so the cache must expire rather than wait for this process to change it.
VERSION_SECONDS = 5


def same_secret(given: str, expected: str) -> bool:
    """Constant-time comparison that also accepts non-ASCII input (hmac refuses non-ASCII str)."""
    return bool(expected) and hmac.compare_digest(given.encode(), expected.encode())


class Sessions:
    def __init__(self, db, signer, secure: bool):
        self.db, self.signer, self.secure = db, signer, secure
        self._version: int | None = None
        self._read_at = 0.0

    def version_stale(self) -> bool:
        return self._version is None or time.monotonic() - self._read_at > VERSION_SECONDS

    def version(self, *, fresh: bool = False) -> int:
        if fresh or self.version_stale():
            self._version, self._read_at = session_version(self.db), time.monotonic()
        return self._version

    def read(self, cookie: str) -> dict | None:
        """{"keep", "version", "age"} for a cookie that is signed, unexpired and of the current version."""
        if not cookie:
            return None
        try:
            payload, signed_at = self.signer.loads(cookie, max_age=KEEP_SECONDS, return_timestamp=True)
        except (BadSignature, SignatureExpired):
            return None
        if payload == SESSION_SUBJECT:
            keep, version = False, 0
        elif isinstance(payload, dict) and payload.get("s") == SESSION_SUBJECT:
            keep, version = bool(payload.get("k")), payload.get("v")
        else:
            return None
        age = time.time() - signed_at.timestamp()
        if age > (KEEP_SECONDS if keep else SESSION_SECONDS) or version != self.version():
            return None
        return {"keep": keep, "version": version, "age": age}

    def issue(self, response, *, keep: bool, version: int | None = None):
        version = self.version(fresh=True) if version is None else version
        response.set_cookie(
            COOKIE,
            self.signer.dumps({"s": SESSION_SUBJECT, "v": version, "k": int(bool(keep))}),
            httponly=True,
            secure=self.secure,
            samesite="strict",
            max_age=KEEP_SECONDS if keep else SESSION_SECONDS,
        )

    def clear(self, response):
        response.delete_cookie(COOKIE, httponly=True, secure=self.secure, samesite="strict")


class Throttle:
    """Ten failed credential checks a minute per client address, shared by the token, password, recovery-code
    and reset routes. Before 2026-10-22 the token and password routes each had their own ten (docs/API.md S3)."""

    def __init__(self, limit: int = 10, window: float = 60.0):
        self.limit, self.window = limit, window
        self.failures: dict[str, list[float]] = defaultdict(list)

    @staticmethod
    def address(request: Request) -> str:
        return request.client.host if request.client else "local"

    def _recent(self, address: str) -> list[float]:
        cutoff = time.monotonic() - self.window
        recent = [t for t in self.failures.get(address, []) if t > cutoff]
        if recent:
            self.failures[address] = recent
        else:
            self.failures.pop(address, None)
        return recent

    def check(self, request: Request):
        recent = self._recent(self.address(request))
        if len(recent) >= self.limit:
            wait = max(1, math.ceil(recent[0] + self.window - time.monotonic()))
            raise HTTPException(
                429,
                f"Too many failed attempts. Try again in {wait} second{'' if wait == 1 else 's'}.",
                headers={"Retry-After": str(wait)},
            )

    def fail(self, request: Request) -> str:
        """Count one failure; the sentence to append to its message when few tries are left."""
        address = self.address(request)
        recent = self._recent(address)
        recent.append(time.monotonic())
        self.failures[address] = recent
        left = max(0, self.limit - len(recent))
        if left == 0:
            wait = max(1, math.ceil(recent[0] + self.window - time.monotonic()))
            return f" Sign-in is paused for {wait} second{'' if wait == 1 else 's'}."
        if left <= 5:
            return f" {left} {'try' if left == 1 else 'tries'} left before sign-in pauses for a minute."
        return ""
