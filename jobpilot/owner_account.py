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
