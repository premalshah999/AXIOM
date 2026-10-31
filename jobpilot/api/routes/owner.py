"""The installation owner's account routes: sign-up, sign-in, recovery, password change, sessions, onboarding.

Refusals are one plain sentence the sign-in screen can show as written. A wrong current password in Setup is a
400, not a 401: the dashboard treats any 401 as "signed out".
"""

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from ...owner_account import (
    ACCOUNT_KEY,
    ONBOARDING_KEY,
    ONBOARDING_STEPS,
    PASSWORD_RULES,
    OnboardingUpdate,
    PasswordChange,
    PasswordLogin,
    PasswordReset,
    Reauthenticate,
    Signup,
    account,
    consume_recovery_code,
    fresh_installation,
    hash_password,
    new_recovery_codes,
    now,
    onboarding_state,
    password_problem,
    recovery_summary,
    replace_recovery_codes,
    revoke_sessions,
    set_password,
    signup_problem,
    split_name,
    verify_password,
)
from ..sessions import KEEP_SECONDS, SESSION_SECONDS, same_secret

NO_PASSWORD = (
    "This installation has no email sign-in yet. Sign in with the access token, then add an email and "
    "password in Settings → System."
)


def router(service, sessions, throttle) -> APIRouter:
    db = service.db
    r = APIRouter()

    def signed_in(request: Request) -> bool:
        return getattr(request.state, "auth", None) is not None

    def with_session(payload: dict, *, keep: bool) -> JSONResponse:
        response = JSONResponse(payload)
        sessions.issue(response, keep=keep)
        return response

    @r.get("/api/account")
    def account_status(request: Request):
        """Public: whether an account exists, whether this installation can create one, and why."""
        stored = account(db)
        exists = bool(stored.get("password_hash"))
        authed = signed_in(request)
        fresh = not exists and fresh_installation(db)
        result = {
            "exists": exists,
            "signup_allowed": fresh or (authed and not exists),
            "signed_in": authed,
            # has_data: a profile or jobs exist without an account, so only the access token opens it.
            "installation": "has_account" if exists else "fresh" if fresh else "has_data",
            "password_rules": list(PASSWORD_RULES),
        }
        if authed:
            session = getattr(request.state, "session", None)
            keep = bool(session and session["keep"])
            result.update(
                name=stored.get("name", ""),
                email=stored.get("email", ""),
                onboarding=onboarding_state(db),
                password_changed_at=stored.get("password_changed_at") or stored.get("created_at"),
                recovery_codes=recovery_summary(stored),
                # via "token": a Bearer request (scripts), which no session limit applies to.
                session={
                    "via": request.state.auth,
                    "keep": keep,
                    "idle_hours": (KEEP_SECONDS if keep else SESSION_SECONDS) // 3600 if session else None,
                },
            )
        return result

    @r.post("/api/account/signup")
    def signup(data: Signup, request: Request):
        throttle.check(request)
        authed = signed_in(request)
        if account(db).get("password_hash"):
            raise HTTPException(409, "An account already exists on this installation. Sign in instead.")
        if not (fresh_installation(db) or authed):
            raise HTTPException(
                403,
                "This installation already holds a profile or jobs, so it can't be claimed by signing up. "
                "Sign in with its access token, then add an email and password in Settings → System.",
            )
        if problem := signup_problem(data.name, data.email, data.password):
            raise ValueError(problem)
        name, email = " ".join(data.name.split()), data.email.strip().lower()
        codes, recovery = new_recovery_codes()
        created = now()
        db.set_setting(
            ACCOUNT_KEY,
            {
                "name": name,
                "email": email,
                "password_hash": hash_password(data.password),
                "created_at": created,
                "password_changed_at": created,
                "recovery": recovery,
            },
        )
        with db.exclusive() as s:
            from ...db import Setting

            row = s.get(Setting, "profile")
            profile = dict(row.value) if row else {}
            first, last = split_name(name)
            for key, value in (
                ("name", name),
                ("email", email),
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
        # The codes are returned this once; only their hashes are stored.
        return with_session(
            {"ok": True, "onboarding": onboarding_state(db), "recovery_codes": codes}, keep=data.keep
        )

    @r.post("/api/account/login")
    def password_login(data: PasswordLogin, request: Request):
        throttle.check(request)
        stored = account(db)
        if not stored.get("password_hash"):
            raise HTTPException(409, NO_PASSWORD)
        email = data.email.strip().lower()
        if not email or not data.password:
            raise ValueError("Enter your email and password")
        # Precise on purpose (owner request 2026-10-22): one owner per installation, and GET /api/account already
        # says whether an account exists. The shared throttle bounds guessing either field.
        if not same_secret(email, stored.get("email", "")):
            raise HTTPException(
                401,
                "No account on this installation uses that email. Check it for typos."
                + throttle.fail(request),
            )
        if not verify_password(data.password, stored["password_hash"]):
            raise HTTPException(
                401, "Wrong password. Use Forgot password if you can't remember it." + throttle.fail(request)
            )
        return with_session({"ok": True, "onboarding": onboarding_state(db)}, keep=data.keep)

    @r.post("/api/account/reset")
    def password_reset(data: PasswordReset, request: Request):
        """Forgot password: a recovery code or the access token proves ownership; every old session ends."""
        throttle.check(request)
        if not account(db).get("password_hash"):
            raise HTTPException(409, NO_PASSWORD)
        secret = data.secret.strip()
        if not secret:
            raise ValueError(
                "Enter one of your recovery codes"
                if data.method == "recovery_code"
                else "Enter the access token"
            )
        # Checked before the proof, so a code is never spent on a password the policy refuses.
        if problem := password_problem(data.password):
            raise ValueError(problem)
        if data.method == "access_token":
            if not same_secret(secret, service.config.app_token):
                raise HTTPException(401, "Incorrect access token." + throttle.fail(request))
        else:
            outcome = consume_recovery_code(db, secret)
            if outcome == "used":
                raise HTTPException(
                    401, "That recovery code was already used. Each code works once." + throttle.fail(request)
                )
            if outcome != "ok":
                raise HTTPException(
                    401,
                    "That recovery code isn't valid. Codes look like 7KQ2M-X9D4F." + throttle.fail(request),
                )
        stored = set_password(db, data.password, reason="reset_" + data.method)
        sessions.version(fresh=True)
        summary = recovery_summary(stored)
        return with_session(
            {
                "ok": True,
                "email": stored.get("email", ""),
                "onboarding": onboarding_state(db),
                "recovery_codes_left": summary["left"] if summary else 0,
            },
            keep=data.keep,
        )

    @r.post("/api/account/password")
    def change_password(data: PasswordChange, request: Request):
        """Signed-in owners only. Other browsers are signed out; this one gets a new cookie."""
        throttle.check(request)
        stored = account(db)
        if not stored.get("password_hash"):
            raise ValueError("No password is set yet. Add an email and password first.")
        if not data.current:
            raise ValueError("Enter your current password")
        if not verify_password(data.current, stored["password_hash"]):
            raise ValueError("Current password is incorrect." + throttle.fail(request))
        if data.new == data.current:
            raise ValueError("Choose a new password that differs from the current one")
        if problem := password_problem(data.new):
            raise ValueError(problem)
        set_password(db, data.new, reason="password_changed")
        sessions.version(fresh=True)
        response = JSONResponse({"ok": True, "other_sessions_ended": True})
        if request.state.auth == "session":
            sessions.issue(response, keep=request.state.session["keep"])
        return response

    @r.post("/api/account/recovery-codes")
    def regenerate_codes(data: Reauthenticate, request: Request):
        """New recovery codes replace every old one; the password is asked again because they reset it."""
        throttle.check(request)
        stored = account(db)
        if not stored.get("password_hash"):
            raise ValueError("Add an email and password first. Recovery codes reset that password.")
        if not data.password:
            raise ValueError("Enter your password")
        if not verify_password(data.password, stored["password_hash"]):
            raise ValueError("Password is incorrect." + throttle.fail(request))
        codes, record = replace_recovery_codes(db)
        return {"codes": codes, "created_at": record["created_at"], "total": len(codes)}

    @r.delete("/api/account/sessions")
    def sign_out_everywhere():
        """End every browser session, this one included. The access token is not a session and keeps working."""
        revoke_sessions(db, "signed_out_everywhere")
        sessions.version(fresh=True)
        response = JSONResponse({"ok": True})
        sessions.clear(response)
        return response

    def onboarding_view():
        from ...features.knowledge import questionnaires

        # Voluntary: never part of "completed", and never blocks applying.
        return {
            **onboarding_state(db),
            "questionnaires": questionnaires.summary(db.get_setting("profile", {}) or {}),
        }

    @r.get("/api/onboarding")
    def onboarding_read():
        return onboarding_view()

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
        return onboarding_view()

    return r
