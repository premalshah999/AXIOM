"""Request security (sign-in, Origin and content-type rules, response headers) and the error mapping."""

import logging
import os
from urllib.parse import urlsplit

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException

from ..owner_account import PUBLIC_PATHS
from ..vocabulary import PROBLEM, plain_error, plain_validation
from .sessions import COOKIE, RENEW_AFTER, same_secret

logger = logging.getLogger(__name__)


LOOPBACK = {"127.0.0.1", "localhost", "::1"}
# The test client's name for itself. It is a plain host name, so a network that answers for it could point a page at this
# computer (DNS rebinding) and reach the routes that need no sign-in, such as creating the owner account on a fresh install.
# Only a running test accepts it; pytest sets this variable and an installation never does.
TEST_HOST = "testserver"


def is_test_host(name: str) -> bool:
    return name == TEST_HOST and "PYTEST_CURRENT_TEST" in os.environ


def host_name(value: str) -> str:
    """The host in a Host header or a URL's netloc, without port or brackets, lower case."""
    value = (value or "").rsplit("@", 1)[-1].strip().lower()
    if value.startswith("["):
        return value[1:].split("]", 1)[0]
    return value.rsplit(":", 1)[0] if value.count(":") == 1 else value


def install(app, config, sessions, cache):
    # Only the names this installation is reached by. A web page whose domain was re-pointed at 127.0.0.1 (DNS rebinding) sends
    # its own domain as Host, so it never matches, whatever the browser believes about the origin.
    served = LOOPBACK | {host_name(urlsplit(config.base_url).netloc)}

    @app.middleware("http")
    async def security(request: Request, call_next):
        path = request.url.path
        session = None
        name = host_name(request.headers.get("host", ""))
        if name not in served and not is_test_host(name):
            return JSONResponse({"detail": "Open AXIOM at its own address (BASE_URL)."}, status_code=421)
        if path.startswith("/api/"):
            if request.method in {"POST", "PUT", "PATCH", "DELETE"} and request.headers.get("sec-fetch-site", "same-origin") not in {
                "same-origin",
                "none",
            }:
                # A browser says where a request came from; "cross-site" and "same-site" (another port on this computer) are
                # never the dashboard. Requests without the header (the app window's sign-in, scripts) are unaffected.
                return JSONResponse({"detail": "This request came from another web page."}, status_code=403)
            origin = request.headers.get("origin")
            if origin and origin.rstrip("/") != config.base_url.rstrip("/"):
                return JSONResponse({"detail": "Origin is not allowed. Check BASE_URL."}, status_code=403)
            # Public routes see who is signed in too (GET /api/account reports it); only the others require it.
            bearer = request.headers.get("authorization", "")
            auth = (
                "token"
                if bearer.startswith("Bearer ") and same_secret(bearer[7:], config.app_token)
                else None
            )
            cookie = request.cookies.get(COOKIE, "")
            if not auth and cookie:
                if sessions.version_stale():
                    # A primary-key read, kept off the event loop (event-loop starvation, 2026-10-12).
                    await run_in_threadpool(sessions.version)
                session = sessions.read(cookie)
                auth = "session" if session else None
            request.state.auth, request.state.session = auth, session
            if not auth and path not in PUBLIC_PATHS:
                detail = (
                    "Your session ended. Sign in again." if cookie else "Sign in with your local access token"
                )
                return JSONResponse({"detail": detail}, status_code=401)
            if request.method in {"POST", "PUT", "PATCH"}:
                kind = request.headers.get("content-type", "")
                if not (kind.startswith("application/json") or kind.startswith("multipart/form-data")):
                    return JSONResponse({"detail": "Use JSON or multipart form data"}, status_code=415)
        response = await call_next(request)
        if (
            session
            and session["age"] > RENEW_AFTER
            and not any(v.startswith(COOKIE + "=") for v in response.headers.getlist("set-cookie"))
        ):
            # Sliding expiry: a session in use is re-signed under the version it was read with, so a cookie
            # that a concurrent password change just ended is not revived.
            sessions.issue(response, keep=session["keep"], version=session["version"])
        if path.startswith("/api/") and request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            cache.mark_stale()
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        if path.startswith("/api/") and "cache-control" not in response.headers:
            # An answer that says how long it keeps (a company's logo, versioned in its address) is left as it is: forcing
            # no-store here made the browser fetch every logo again on every page and every row (measured 2026-11-01).
            response.headers["Cache-Control"] = "no-store"
        return response

    # What a refusal says goes on a screen as it stands, so these are the one door every error leaves by. A validator's report is a
    # ValueError too, with its link ("For further information visit https://errors.pydantic.dev/..."), and any library's message
    # could be next: a sentence the app wrote stands, anything else becomes a plain one.
    @app.exception_handler(ValidationError)
    async def invalid_data(_, exc):
        return JSONResponse({"detail": plain_validation(exc.errors(include_input=False, include_context=False))}, status_code=400)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_, exc):
        return JSONResponse({"detail": plain_validation(exc.errors())}, status_code=422)

    @app.exception_handler(HTTPException)
    async def refused(_, exc):
        # Routes hand on str(exc) as the refusal, a validator's report among them.
        detail = plain_error(exc.detail) if isinstance(exc.detail, str) else exc.detail
        return JSONResponse({"detail": detail}, status_code=exc.status_code, headers=exc.headers)

    @app.exception_handler(ValueError)
    async def invalid(_, exc):
        return JSONResponse({"detail": plain_error(str(exc)[:1000])}, status_code=400)

    @app.exception_handler(RuntimeError)
    async def runtime_error(_, exc):
        return JSONResponse({"detail": plain_error(str(exc)[:500])}, status_code=409)

    @app.exception_handler(Exception)
    async def unexpected(_, exc):
        # Whatever nothing else caught: the log keeps it, the screen says something a person can use.
        logger.exception("Unhandled error in a request", exc_info=exc)
        return JSONResponse({"detail": PROBLEM}, status_code=500)
