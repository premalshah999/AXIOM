"""Request security (sign-in, Origin and content-type rules, response headers) and the error mapping."""

import hmac

from fastapi import Request
from fastapi.responses import JSONResponse
from itsdangerous import BadSignature, SignatureExpired

from ..owner_account import PUBLIC_PATHS as PUBLIC_ACCOUNT_PATHS


def install(app, config, signer, cache):
    @app.middleware("http")
    async def security(request: Request, call_next):
        path = request.url.path
        if path.startswith("/api/"):
            origin = request.headers.get("origin")
            if origin and origin.rstrip("/") != config.base_url.rstrip("/"):
                return JSONResponse({"detail": "Origin is not allowed. Check BASE_URL."}, status_code=403)
            if path != "/api/login" and path not in PUBLIC_ACCOUNT_PATHS:
                bearer = request.headers.get("authorization", "")
                valid = bearer.startswith("Bearer ") and hmac.compare_digest(bearer[7:], config.app_token)
                if not valid:
                    try:
                        valid = (
                            signer.loads(request.cookies.get("jp_session", ""), max_age=86400) == "applicant"
                        )
                    except (BadSignature, SignatureExpired):
                        valid = False
                if not valid:
                    return JSONResponse({"detail": "Sign in with your local access token"}, status_code=401)
            if request.method in {"POST", "PUT", "PATCH"}:
                kind = request.headers.get("content-type", "")
                if not (kind.startswith("application/json") or kind.startswith("multipart/form-data")):
                    return JSONResponse({"detail": "Use JSON or multipart form data"}, status_code=415)
        response = await call_next(request)
        if path.startswith("/api/") and request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            cache.mark_stale()
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        if path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(ValueError)
    async def invalid(_, exc):
        return JSONResponse({"detail": str(exc)[:1000]}, status_code=400)

    @app.exception_handler(RuntimeError)
    async def runtime_error(_, exc):
        return JSONResponse({"detail": str(exc)[:500]}, status_code=409)
