"""The installation owner's account: first-run sign-up, email/password sign-in and onboarding state.

ApplyPilot is single-tenant: one owner, one profile. Anonymous sign-up is therefore allowed only on a
fresh installation (no account, no profile identity, no jobs). An installation that already holds
someone's data can add a password only from an authenticated session (the access token keeps
working for operators and scripts), so a visitor can never create the account that unlocks it.
"""

import base64
import hashlib
import hmac
import os
import re
import time
from collections import defaultdict
from datetime import UTC, datetime

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select

ACCOUNT_KEY = "owner_account"
ONBOARDING_KEY = "onboarding"
SESSION_SUBJECT = "applicant"
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PUBLIC_PATHS = {"/api/account", "/api/account/signup", "/api/account/login"}
ONBOARDING_STEPS = (
    "resume",
    "contact",
    "authorization",
    "preferences",
    "background",
    "identity",
    "permissions",
    "review",
)
_SCRYPT = {"n": 2**14, "r": 8, "p": 1}


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, dklen=32, **_SCRYPT)
    return "scrypt$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(digest).decode()


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, salt, digest = stored.split("$")
        if scheme != "scrypt":
            return False
        expected = base64.b64decode(digest)
        actual = hashlib.scrypt(
            password.encode(), salt=base64.b64decode(salt), dklen=len(expected), **_SCRYPT
        )
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def password_problem(password: str) -> str:
    if len(password) < 10:
        return "Use at least 10 characters"
    if len(password) > 200:
        return "Use at most 200 characters"
    if password.lower() == password or password.upper() == password or not re.search(r"\d", password):
        return "Mix upper and lower case letters with at least one number"
    return ""


class Signup(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=200)

    @field_validator("email")
    @classmethod
    def valid_email(cls, value: str) -> str:
        value = value.strip().lower()
        if not EMAIL.match(value):
            raise ValueError("Enter a valid email address")
        return value


class PasswordLogin(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=200)


class PasswordChange(BaseModel):
    current: str = Field(min_length=1, max_length=200)
    new: str = Field(min_length=1, max_length=200)


class OnboardingUpdate(BaseModel):
    step: str = Field(default="", max_length=40)
    completed: bool = False


def account(db) -> dict:
    return db.get_setting(ACCOUNT_KEY, {}) or {}


def fresh_installation(db) -> bool:
    """No owner and no one's data: the only state in which a visitor may create the account."""
    from .db import Job

    if account(db).get("password_hash"):
        return False
    profile = db.get_setting("profile", {}) or {}
    if any(
        str(profile.get(key) or "").strip() for key in ("name", "email", "first_name", "last_name", "phone")
    ):
        return False
    with db.session() as s:
        return not s.scalar(select(func.count()).select_from(Job))


def onboarding_state(db) -> dict:
    state = dict(db.get_setting(ONBOARDING_KEY, {}) or {})
    if "completed" not in state:
        # Installations set up before onboarding existed already hold a working profile.
        profile = db.get_setting("profile", {}) or {}
        state["completed"] = bool(profile.get("email") and (profile.get("work") or profile.get("education")))
    state.setdefault("step", ONBOARDING_STEPS[0])
    return state


def split_name(name: str) -> tuple[str, str]:
    parts = name.strip().split()
    return (parts[0], " ".join(parts[1:])) if len(parts) > 1 else (name.strip(), "")


def install(app, service, signer, secure_cookies: bool):
    db = service.db
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

    @app.get("/api/account")
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

    @app.post("/api/account/signup")
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
            from .db import Setting

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

    @app.post("/api/account/login")
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

    @app.post("/api/account/password")
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

    @app.get("/api/profile")
    def profile_read():
        from .schemas import Profile

        return Profile.model_validate(db.get_setting("profile", {}) or {}).model_dump(
            exclude={"reviewed_answers", "approved_answers", "forgotten_answers"}
        )

    @app.get("/api/resumes")
    def resume_list():
        """Uploaded resume variants without their text (the onboarding resume step reads this)."""
        from .db import Resume

        with db.session() as s:
            rows = s.scalars(
                select(Resume).where(Resume.demo.is_(False)).order_by(Resume.created_at.desc())
            ).all()
            return [
                {"id": r.id, "name": r.name, "roles": list(r.roles or []), "created_at": r.created_at}
                for r in rows
            ]

    @app.get("/api/onboarding")
    def onboarding_read():
        return onboarding_state(db)

    @app.put("/api/onboarding")
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
