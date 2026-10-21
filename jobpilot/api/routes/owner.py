"""The installation owner's account routes: first-run sign-up, password sign-in, password change, onboarding."""

import hmac
import time
from collections import defaultdict
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from ...owner_account import (
    ACCOUNT_KEY,
    ONBOARDING_KEY,
    ONBOARDING_STEPS,
    SESSION_SUBJECT,
    OnboardingUpdate,
    PasswordChange,
    PasswordLogin,
    Signup,
    account,
    fresh_installation,
    hash_password,
    onboarding_state,
    password_problem,
    split_name,
    verify_password,
)


def router(service, signer, secure_cookies: bool) -> APIRouter:
    db = service.db
    r = APIRouter()
    attempts: dict[str, list[float]] = defaultdict(list)

    def throttle(request: Request):
        addr = request.client.host if request.client else "local"
        attempts[addr] = [t for t in attempts[addr] if t > time.monotonic() - 60]
        if len(attempts[addr]) >= 10:
            raise HTTPException(429, "Too many attempts. Try again in a minute.")
        attempts[addr].append(time.monotonic())

    def signed_in(request: Request) -> bool:
        from itsdangerous import BadSignature, SignatureExpired

        bearer = request.headers.get("authorization", "")
        if bearer.startswith("Bearer ") and hmac.compare_digest(bearer[7:], service.config.app_token):
            return True
        try:
            return signer.loads(request.cookies.get("jp_session", ""), max_age=86400) == SESSION_SUBJECT
        except (BadSignature, SignatureExpired):
            return False

    def session_response(payload: dict) -> JSONResponse:
        response = JSONResponse(payload)
        response.set_cookie(
            "jp_session",
            signer.dumps(SESSION_SUBJECT),
            httponly=True,
            secure=secure_cookies,
            samesite="strict",
            max_age=86400,
        )
        return response

    @r.get("/api/account")
    def account_status(request: Request):
        """Public: only whether an account exists and whether this installation can create one."""
        stored = account(db)
        authed = signed_in(request)
        result = {
            "exists": bool(stored.get("password_hash")),
            "signup_allowed": fresh_installation(db) or (authed and not stored.get("password_hash")),
            "signed_in": authed,
        }
        if authed:
            result.update(
                name=stored.get("name", ""), email=stored.get("email", ""), onboarding=onboarding_state(db)
            )
        return result

    @r.post("/api/account/signup")
    def signup(data: Signup, request: Request):
        throttle(request)
        authed = signed_in(request)
        if account(db).get("password_hash"):
            raise HTTPException(409, "An account already exists. Sign in instead.")
        if not (fresh_installation(db) or authed):
            raise HTTPException(
                403, "This installation already has data. Sign in with its access token first."
            )
        if problem := password_problem(data.password):
            raise ValueError(problem)
        name = " ".join(data.name.split())
        db.set_setting(
            ACCOUNT_KEY,
            {
                "name": name,
                "email": data.email,
                "password_hash": hash_password(data.password),
                "created_at": datetime.now(UTC).isoformat(),
            },
        )
        with db.exclusive() as s:
            from ...db import Setting

            row = s.get(Setting, "profile")
            profile = dict(row.value) if row else {}
            first, last = split_name(name)
            for key, value in (
                ("name", name),
                ("email", data.email),
                ("first_name", first),
                ("last_name", last),
            ):
                if not str(profile.get(key) or "").strip():
                    profile[key] = value
            if row:
                row.value = profile
            else:
                s.add(Setting(key="profile", value=profile))
        if not authed:
            db.set_setting(ONBOARDING_KEY, {"completed": False, "step": ONBOARDING_STEPS[0]})
        return session_response({"ok": True, "onboarding": onboarding_state(db)})

    @r.post("/api/account/login")
    def password_login(data: PasswordLogin, request: Request):
        throttle(request)
        stored = account(db)
        ok = (
            stored.get("password_hash")
            and hmac.compare_digest(stored.get("email", ""), data.email.strip().lower())
            and verify_password(data.password, stored["password_hash"])
        )
        if not ok:
            raise HTTPException(401, "Email or password is incorrect")
        return session_response({"ok": True, "onboarding": onboarding_state(db)})

    @r.post("/api/account/password")
    def change_password(data: PasswordChange, request: Request):
        """Signed-in owners only. A wrong current password is a 400: the dashboard treats 401 as signed out."""
        throttle(request)
        stored = account(db)
        if not stored.get("password_hash"):
            raise ValueError("No password is set yet. Add an email and password first.")
        if not verify_password(data.current, stored["password_hash"]):
            raise ValueError("Current password is incorrect")
        if problem := password_problem(data.new):
            raise ValueError(problem)
        db.set_setting(
            ACCOUNT_KEY,
            {
                **stored,
                "password_hash": hash_password(data.new),
                "password_changed_at": datetime.now(UTC).isoformat(),
            },
        )
        return {"ok": True}

    @r.get("/api/onboarding")
    def onboarding_read():
        return onboarding_state(db)

    @r.put("/api/onboarding")
    def onboarding_write(data: OnboardingUpdate):
        if data.step and data.step not in ONBOARDING_STEPS:
            raise ValueError("Unknown onboarding step")
        state = onboarding_state(db)
        state.update(
            step=data.step or state["step"],
            completed=data.completed,
            updated_at=datetime.now(UTC).isoformat(),
        )
        db.set_setting(ONBOARDING_KEY, state)
        return state

    return r
