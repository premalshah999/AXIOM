"""The installation owner's account: first-run sign-up, email/password sign-in, recovery and onboarding state.

ApplyPilot is single-tenant: one owner, one profile. Anonymous sign-up is therefore allowed only on a
fresh installation (no account, no profile identity, no jobs). An installation that already holds
someone's data can add a password only from an authenticated session (the access token keeps
working for operators and scripts), so a visitor can never create the account that unlocks it.

A self-hosted owner who forgets the password has no email reset: the installation's Gmail access is
read-only. Three proofs of ownership replace it (2026-10-22): a one-time recovery code shown at sign-up,
the installation's access token, or `jobpilot reset-password` on the host. Every reset and password change
raises the session version, which ends the sessions signed before it.
"""

import base64
import hashlib
import hmac
import os
import re
import secrets
from datetime import UTC, datetime

from pydantic import BaseModel, Field
from sqlalchemy import func, select

ACCOUNT_KEY = "owner_account"
ONBOARDING_KEY = "onboarding"
SESSIONS_KEY = "owner_sessions"
SESSION_SUBJECT = "applicant"
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
# Reachable without a credential. Sign-out is public so that a browser whose session already ended can still
# clear its cookie (docs/API.md I6).
PUBLIC_PATHS = {
    "/api/login",
    "/api/logout",
    "/api/account",
    "/api/account/signup",
    "/api/account/login",
    "/api/account/reset",
}
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
# Shown before the owner types, and enforced by password_problem: keep the two in step.
PASSWORD_RULES = (
    "At least 10 characters",
    "Upper and lower case letters",
    "At least one number",
)
RECOVERY_CODES = 10
# Crockford base32 without I, L, O and U: nothing to misread when the code is typed back from paper.
_CODE_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
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


def email_problem(email: str) -> str:
    if not EMAIL.match(email.strip()):
        return "Enter an email address like name@example.com"
    if len(email.strip()) > 254:
        return "Use an email address of at most 254 characters"
    return ""


def now() -> str:
    return datetime.now(UTC).isoformat()


# Request bodies take plain strings and are checked in the handlers, so every refusal is one plain sentence
# (a pydantic 422 reads "body › email: Value error, ..." in the sign-up form).
class Signup(BaseModel):
    name: str = Field(default="", max_length=120)
    email: str = Field(default="", max_length=320)
    password: str = Field(default="", max_length=200)
    keep: bool = False


class PasswordLogin(BaseModel):
    email: str = Field(default="", max_length=320)
    password: str = Field(default="", max_length=200)
    keep: bool = False


class PasswordChange(BaseModel):
    current: str = Field(default="", max_length=200)
    new: str = Field(default="", max_length=200)


class PasswordReset(BaseModel):
    """Forgot password: prove ownership with a recovery code or the access token, then set a new password."""

    method: str = Field(pattern="^(recovery_code|access_token)$")
    secret: str = Field(default="", max_length=256)
    password: str = Field(default="", max_length=200)
    keep: bool = False


class Reauthenticate(BaseModel):
    password: str = Field(default="", max_length=200)


class OnboardingUpdate(BaseModel):
    step: str = Field(default="", max_length=40)
    completed: bool = False


def signup_problem(name: str, email: str, password: str) -> str:
    if not " ".join(name.split()):
        return "Enter your full name"
    return email_problem(email) or password_problem(password)


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


# ---------------------------------------------------------------------------------------------------------------
# Sessions: a version number that every cookie carries. Raising it ends every session signed before.
# ---------------------------------------------------------------------------------------------------------------


def session_version(db) -> int:
    return int((db.get_setting(SESSIONS_KEY, {}) or {}).get("version", 0))


def revoke_sessions(db, reason: str) -> int:
    """End every signed-in browser. The access token (Bearer) is the installation's own key and keeps working."""
    from .db import Setting

    with db.exclusive() as s:
        row = s.get(Setting, SESSIONS_KEY)
        current = dict(row.value) if row else {}
        version = int(current.get("version", 0)) + 1
        value = {"version": version, "revoked_at": now(), "reason": reason}
        if row:
            row.value = value
        else:
            s.add(Setting(key=SESSIONS_KEY, value=value))
    return version


def set_password(db, password: str, *, reason: str) -> dict:
    """Store a new password hash and end every existing session. The caller has checked the policy."""
    stored = account(db)
    stored = {**stored, "password_hash": hash_password(password), "password_changed_at": now()}
    db.set_setting(ACCOUNT_KEY, stored)
    revoke_sessions(db, reason)
    return stored


# ---------------------------------------------------------------------------------------------------------------
# Recovery codes: shown once, stored as salted hashes, each usable once.
# ---------------------------------------------------------------------------------------------------------------


def normalize_code(code: str) -> str:
    """Upper case without spaces or dashes; O, I and L are read as the digits they resemble."""
    value = re.sub(r"[\s-]", "", code).upper()
    return value.translate(str.maketrans({"O": "0", "I": "1", "L": "1"}))


def _code_hash(salt: str, code: str) -> str:
    return hmac.new(base64.b64decode(salt), normalize_code(code).encode(), hashlib.sha256).hexdigest()


def new_recovery_codes(count: int = RECOVERY_CODES) -> tuple[list[str], dict]:
    """Fresh codes for the owner to keep, and the record that stores only their hashes. Each code carries
    50 random bits; sign-in attempts are throttled, so a hash per code with one random salt is enough."""
    salt = base64.b64encode(os.urandom(16)).decode()
    codes = []
    while len(codes) < count:
        raw = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(10))
        code = raw[:5] + "-" + raw[5:]
        if code not in codes:
            codes.append(code)
    record = {
        "salt": salt,
        "created_at": now(),
        "codes": [{"hash": _code_hash(salt, code), "used_at": None} for code in codes],
    }
    return codes, record


def recovery_summary(stored: dict) -> dict | None:
    record = stored.get("recovery") or {}
    codes = record.get("codes") or []
    if not codes:
        return None
    return {
        "left": sum(1 for c in codes if not c.get("used_at")),
        "total": len(codes),
        "created_at": record.get("created_at"),
    }


def check_recovery_code(stored: dict, code: str) -> tuple[str, int | None]:
    """("ok", index) for an unused matching code; ("used", None) for a spent one; ("invalid", None) otherwise.
    Every stored hash is compared, so the answer takes the same time wherever the code sits in the list."""
    record = stored.get("recovery") or {}
    salt = record.get("salt")
    if not salt or not normalize_code(code):
        return "invalid", None
    candidate = _code_hash(salt, code)
    found, spent = None, False
    for index, entry in enumerate(record.get("codes") or []):
        if hmac.compare_digest(candidate, str(entry.get("hash", ""))):
            if entry.get("used_at"):
                spent = True
            else:
                found = index
    if found is not None:
        return "ok", found
    return ("used" if spent else "invalid"), None


def spend_recovery_code(stored: dict, index: int) -> dict:
    record = dict(stored["recovery"])
    codes = [dict(c) for c in record["codes"]]
    codes[index]["used_at"] = now()
    return {**stored, "recovery": {**record, "codes": codes}}


def consume_recovery_code(db, code: str) -> str:
    """Check and spend one code in one transaction, so two resets cannot both use it. "ok", "used" or "invalid"."""
    from .db import Setting

    with db.exclusive() as s:
        row = s.get(Setting, ACCOUNT_KEY)
        stored = dict(row.value) if row else {}
        outcome, index = check_recovery_code(stored, code)
        if outcome == "ok":
            row.value = spend_recovery_code(stored, index)
    return outcome


def replace_recovery_codes(db) -> tuple[list[str], dict]:
    codes, record = new_recovery_codes()
    from .db import Setting

    with db.exclusive() as s:
        row = s.get(Setting, ACCOUNT_KEY)
        row.value = {**dict(row.value), "recovery": record}
    return codes, record
