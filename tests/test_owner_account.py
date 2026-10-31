"""The owner's account: sign-up (2026-10-15) and the 2026-10-22 sign-in, recovery and session rules.

Anonymous sign-up only on a fresh installation; an installation that holds data needs the access token first.
Sign-in errors are precise and share one throttle. A forgotten password is reset with a one-time recovery code,
the access token or `jobpilot reset-password`; every reset, password change and "Sign out everywhere" ends the
sessions signed before it. Sessions renew while used, and "Keep me signed in" lasts 30 days.
"""

import json
import time

from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner, URLSafeTimedSerializer

from jobpilot.cli import reset_password
from jobpilot.owner_account import (
    ACCOUNT_KEY,
    check_recovery_code,
    hash_password,
    new_recovery_codes,
    normalize_code,
    password_problem,
    verify_password,
)

GOOD = "Correct-Horse-42"
NEW = "Another-Pass-77"
OWNER = {"name": "Alex Doe", "email": "applicant@example.com", "password": GOOD}


def anonymous(app, config):
    return TestClient(app, base_url=config.base_url)


def signed_up(app, config, **extra):
    web = anonymous(app, config)
    answer = web.post("/api/account/signup", json={**OWNER, **extra})
    assert answer.status_code == 200, answer.text
    return web, answer.json()["recovery_codes"]


def signed_in(app, config, password=GOOD, **extra):
    web = anonymous(app, config)
    answer = web.post("/api/account/login", json={"email": OWNER["email"], "password": password, **extra})
    assert answer.status_code == 200, answer.text
    return web


def aged_cookie(config, payload, age):
    class Aged(TimestampSigner):
        def get_timestamp(self):
            return int(time.time() - age)

    return URLSafeTimedSerializer(config.app_token, salt="applypilot-session-v1", signer=Aged).dumps(payload)


def session_cookie(answer):
    return next(v for v in answer.headers.get_list("set-cookie") if v.startswith("jp_session="))


# ---------- password and recovery-code primitives ----------


def test_password_hash_round_trip_and_policy():
    stored = hash_password(GOOD)
    assert (
        stored.startswith("scrypt$") and verify_password(GOOD, stored) and not verify_password("nope", stored)
    )
    assert password_problem("short1A") and password_problem("alllowercase123") and not password_problem(GOOD)


def test_recovery_codes_are_stored_only_as_hashes_and_read_forgivingly():
    codes, record = new_recovery_codes()
    assert len(codes) == len(set(codes)) == 10 and all(len(c) == 11 and c[5] == "-" for c in codes)
    assert not any(code in json.dumps(record) or normalize_code(code) in json.dumps(record) for code in codes)
    stored = {"recovery": record}
    assert check_recovery_code(stored, codes[3])[0] == "ok"
    # Typed back from paper: lower case, no dash, O for 0 and l for 1.
    sloppy = codes[3].replace("-", " ").lower().replace("0", "o").replace("1", "l")
    assert check_recovery_code(stored, sloppy) == ("ok", 3)
    assert check_recovery_code(stored, "AAAAA-AAAAA")[0] == "invalid"


# ---------- new user ----------


def test_fresh_installation_signup_seeds_profile_starts_onboarding_and_returns_codes_once(
    app, config, service
):
    web = anonymous(app, config)
    status = web.get("/api/account").json()
    assert status["exists"] is False and status["signup_allowed"] is True and status["signed_in"] is False
    assert status["installation"] == "fresh" and len(status["password_rules"]) == 3
    r = web.post("/api/account/signup", json={**OWNER, "email": "Applicant@Example.com"})
    assert r.status_code == 200 and r.json()["onboarding"]["completed"] is False
    codes = r.json()["recovery_codes"]
    assert len(codes) == 10
    stored = json.dumps(service.db.get_setting(ACCOUNT_KEY))
    assert not any(code in stored for code in codes) and GOOD not in stored
    # The session cookie now opens the API.
    assert web.get("/api/control").status_code == 200
    profile = web.get("/api/profile").json()
    assert (profile["first_name"], profile["last_name"], profile["email"]) == (
        "Alex",
        "Doe",
        "applicant@example.com",
    )
    assert "reviewed_answers" not in profile and "approved_answers" not in profile
    me = web.get("/api/account").json()
    assert me["recovery_codes"]["left"] == 10 and "password_hash" not in me and "recovery" not in me
    # A second account is never created.
    again = anonymous(app, config).post("/api/account/signup", json={**OWNER, "email": "other@example.com"})
    assert again.status_code == 409


def test_signup_refusals_are_one_plain_sentence(app, config):
    web = anonymous(app, config)

    def refusal(**changes):
        answer = web.post("/api/account/signup", json={**OWNER, **changes})
        assert answer.status_code == 400, answer.text
        return answer.json()["detail"]

    assert refusal(name="  ") == "Enter your full name"
    assert refusal(email="applicant.example.com") == "Enter an email address like name@example.com"
    assert refusal(password="short1A") == "Use at least 10 characters"
    assert refusal(password="all-lower-case-1") == "Mix upper and lower case letters with at least one number"
    assert web.get("/api/account").json()["exists"] is False


def test_installation_with_data_refuses_anonymous_signup_and_says_why(app, config, client):
    client.put("/api/profile", json={"name": "Alex Doe", "email": "applicant@example.com"})
    web = anonymous(app, config)
    status = web.get("/api/account").json()
    assert status["signup_allowed"] is False and status["installation"] == "has_data"
    r = web.post("/api/account/signup", json={**OWNER, "name": "Visitor"})
    assert r.status_code == 403 and "access token" in r.json()["detail"]
    # The signed-in owner (token) can add a password, gets recovery codes, then signs in with it.
    added = client.post("/api/account/signup", json=OWNER)
    assert added.status_code == 200 and len(added.json()["recovery_codes"]) == 10
    assert anonymous(app, config).get("/api/account").json()["installation"] == "has_account"
    ok = web.post("/api/account/login", json={"email": "APPLICANT@example.com", "password": GOOD})
    assert ok.status_code == 200 and web.get("/api/control").status_code == 200


# ---------- returning user ----------


def test_sign_in_errors_say_exactly_what_is_wrong(app, config, client):
    web = anonymous(app, config)
    none = web.post("/api/account/login", json={"email": OWNER["email"], "password": GOOD})
    assert none.status_code == 409 and "access token" in none.json()["detail"]
    client.post("/api/account/signup", json=OWNER)
    empty = web.post("/api/account/login", json={"email": "", "password": ""})
    assert empty.status_code == 400 and empty.json()["detail"] == "Enter your email and password"
    other = web.post("/api/account/login", json={"email": "someone@example.com", "password": GOOD})
    assert other.status_code == 401 and other.json()["detail"].startswith(
        "No account on this installation uses that email"
    )
    wrong = web.post("/api/account/login", json={"email": OWNER["email"], "password": "Wrong-Pass-11"})
    assert wrong.status_code == 401 and wrong.json()["detail"].startswith("Wrong password.")
    assert web.get("/api/control").status_code == 401


def test_password_and_token_failures_share_one_throttle_that_says_when_to_retry(app, config, client):
    client.post("/api/account/signup", json=OWNER)
    web = anonymous(app, config)
    details = [
        web.post("/api/account/login", json={"email": OWNER["email"], "password": f"Wrong-Pass-{n}"}).json()[
            "detail"
        ]
        for n in range(5)
    ]
    assert "5 tries left" in details[-1] and "tries left" not in details[0]
    for _ in range(4):
        assert web.post("/api/login", json={"token": "wrong"}).status_code == 401
    last = web.post("/api/login", json={"token": "wrong"})
    assert last.status_code == 401 and "Sign-in is paused" in last.json()["detail"]
    blocked = web.post("/api/account/login", json={"email": OWNER["email"], "password": GOOD})
    assert blocked.status_code == 429 and int(blocked.headers["retry-after"]) >= 1
    assert blocked.json()["detail"].startswith("Too many failed attempts. Try again in ")
    assert (
        web.post(
            "/api/account/reset", json={"method": "access_token", "secret": config.app_token, "password": NEW}
        ).status_code
        == 429
    )


def test_keep_me_signed_in_lasts_thirty_days_and_an_ordinary_session_one(app, config, client):
    client.post("/api/account/signup", json=OWNER)
    web = anonymous(app, config)
    ordinary = web.post("/api/account/login", json={"email": OWNER["email"], "password": GOOD})
    kept = web.post("/api/account/login", json={"email": OWNER["email"], "password": GOOD, "keep": True})
    assert "Max-Age=86400" in session_cookie(ordinary)
    kept_cookie = session_cookie(kept)
    assert "Max-Age=2592000" in kept_cookie and "HttpOnly" in kept_cookie and "SameSite=strict" in kept_cookie
    me = web.get("/api/account").json()
    assert me["session"] == {"via": "session", "keep": True, "idle_hours": 720}
    assert client.get("/api/account").json()["session"]["via"] == "token"


def test_a_session_in_use_is_renewed_so_it_never_expires_mid_work(app, config, client, monkeypatch):
    client.post("/api/account/signup", json=OWNER)
    web = signed_in(app, config)
    assert not any(
        v.startswith("jp_session=") for v in web.get("/api/control").headers.get_list("set-cookie")
    )
    monkeypatch.setattr("jobpilot.api.middleware.RENEW_AFTER", -1)
    renewed = web.get("/api/control")
    assert renewed.status_code == 200 and "Max-Age=86400" in session_cookie(renewed)
    # Bearer requests are not sessions and get no cookie.
    assert not any(
        v.startswith("jp_session=") for v in client.get("/api/control").headers.get_list("set-cookie")
    )


def test_cookie_age_limits_and_cookies_signed_before_session_versions(app, config):
    def opens(cookie):
        web = anonymous(app, config)
        web.cookies.set("jp_session", cookie)
        return web.get("/api/control").status_code == 200

    assert opens(aged_cookie(config, "applicant", 3600))  # the old constant keeps working for its day
    assert not opens(aged_cookie(config, "applicant", 2 * 86400))
    assert opens(aged_cookie(config, {"s": "applicant", "v": 0, "k": 1}, 20 * 86400))
    assert not opens(aged_cookie(config, {"s": "applicant", "v": 0, "k": 1}, 31 * 86400))
    assert not opens(aged_cookie(config, {"s": "applicant", "v": 0, "k": 0}, 2 * 86400))
    assert not opens(aged_cookie(config, {"s": "someone-else", "v": 0, "k": 0}, 60))


def test_an_ended_session_is_told_so_and_can_still_sign_out(app, config):
    web = anonymous(app, config)
    assert web.get("/api/control").json()["detail"] == "Sign in with your local access token"
    web.cookies.set("jp_session", aged_cookie(config, "applicant", 2 * 86400))
    assert web.get("/api/control").json()["detail"] == "Your session ended. Sign in again."
    out = web.post("/api/logout", json={})
    assert out.status_code == 200 and 'jp_session=""' in out.headers["set-cookie"]


def test_sign_out_everywhere_ends_every_browser_but_not_the_access_token(app, config, client):
    client.post("/api/account/signup", json=OWNER)
    laptop, phone = signed_in(app, config), signed_in(app, config, keep=True)
    legacy = anonymous(app, config)
    legacy.cookies.set("jp_session", aged_cookie(config, "applicant", 60))
    assert all(b.get("/api/control").status_code == 200 for b in (laptop, phone, legacy))
    assert laptop.delete("/api/account/sessions").json() == {"ok": True}
    for browser in (laptop, phone, legacy):
        assert browser.get("/api/control").status_code == 401
    assert client.get("/api/control").status_code == 200
    assert signed_in(app, config).get("/api/control").status_code == 200


def test_owner_changes_password_and_other_browsers_are_signed_out(app, config, client):
    client.post("/api/account/signup", json=OWNER)
    here, elsewhere = signed_in(app, config, keep=True), signed_in(app, config)
    wrong = here.post("/api/account/password", json={"current": "Not-The-Pass1", "new": NEW})
    assert wrong.status_code == 400 and "incorrect" in wrong.json()["detail"]
    assert here.post("/api/account/password", json={"current": GOOD, "new": GOOD}).status_code == 400
    assert here.post("/api/account/password", json={"current": GOOD, "new": "short"}).status_code == 400
    changed = here.post("/api/account/password", json={"current": GOOD, "new": NEW})
    assert changed.json() == {"ok": True, "other_sessions_ended": True}
    assert "Max-Age=2592000" in session_cookie(changed)  # this browser stays signed in, still kept
    assert here.get("/api/control").status_code == 200
    assert elsewhere.get("/api/control").status_code == 401
    web = anonymous(app, config)
    assert web.post("/api/account/login", json={"email": OWNER["email"], "password": GOOD}).status_code == 401
    assert web.post("/api/account/login", json={"email": OWNER["email"], "password": NEW}).status_code == 200
    # Anonymous visitors cannot change it.
    stranger = anonymous(app, config).post(
        "/api/account/password", json={"current": NEW, "new": "Third-Pass-999"}
    )
    assert stranger.status_code == 401


# ---------- forgot / reset password ----------


def test_a_recovery_code_resets_the_password_once_and_ends_old_sessions(app, config):
    old_session, codes = signed_up(app, config)
    web = anonymous(app, config)
    reset = web.post(
        "/api/account/reset",
        json={"method": "recovery_code", "secret": codes[0].lower().replace("-", ""), "password": NEW},
    )
    assert reset.status_code == 200, reset.text
    assert reset.json()["recovery_codes_left"] == 9 and reset.json()["email"] == OWNER["email"]
    assert web.get("/api/control").status_code == 200  # signed in by the reset
    assert old_session.get("/api/control").status_code == 401
    again = anonymous(app, config).post(
        "/api/account/reset",
        json={"method": "recovery_code", "secret": codes[0], "password": "Third-Pass-999"},
    )
    assert again.status_code == 401 and again.json()["detail"].startswith(
        "That recovery code was already used"
    )
    invented = anonymous(app, config).post(
        "/api/account/reset",
        json={"method": "recovery_code", "secret": "ZZZZZ-ZZZZZ", "password": "Third-Pass-999"},
    )
    assert invented.status_code == 401 and invented.json()["detail"].startswith(
        "That recovery code isn't valid"
    )
    assert signed_in(app, config, password=NEW).get("/api/account").json()["recovery_codes"]["left"] == 9


def test_a_weak_new_password_does_not_spend_the_recovery_code(app, config):
    _, codes = signed_up(app, config)
    weak = anonymous(app, config).post(
        "/api/account/reset", json={"method": "recovery_code", "secret": codes[1], "password": "weak"}
    )
    assert weak.status_code == 400 and weak.json()["detail"] == "Use at least 10 characters"
    ok = anonymous(app, config).post(
        "/api/account/reset", json={"method": "recovery_code", "secret": codes[1], "password": NEW}
    )
    assert ok.status_code == 200 and ok.json()["recovery_codes_left"] == 9


def test_the_access_token_resets_the_password(app, config):
    old_session, _ = signed_up(app, config)
    web = anonymous(app, config)
    wrong = web.post(
        "/api/account/reset", json={"method": "access_token", "secret": "not-it", "password": NEW}
    )
    assert wrong.status_code == 401 and wrong.json()["detail"].startswith("Incorrect access token")
    ok = web.post(
        "/api/account/reset",
        json={"method": "access_token", "secret": f" {config.app_token} ", "password": NEW},
    )
    assert ok.status_code == 200 and web.get("/api/control").status_code == 200
    assert old_session.get("/api/control").status_code == 401
    assert signed_in(app, config, password=NEW)


def test_reset_needs_an_account_and_a_known_method(app, config):
    web = anonymous(app, config)
    none = web.post(
        "/api/account/reset", json={"method": "access_token", "secret": config.app_token, "password": NEW}
    )
    assert none.status_code == 409 and "Settings → System" in none.json()["detail"]
    assert (
        web.post("/api/account/reset", json={"method": "email", "secret": "x", "password": NEW}).status_code
        == 422
    )


def test_regenerated_recovery_codes_replace_the_old_ones(app, config):
    web, old_codes = signed_up(app, config)
    wrong = web.post("/api/account/recovery-codes", json={"password": "Wrong-Pass-11"})
    assert wrong.status_code == 400 and wrong.json()["detail"].startswith("Password is incorrect")
    fresh = web.post("/api/account/recovery-codes", json={"password": GOOD})
    assert fresh.status_code == 200 and len(fresh.json()["codes"]) == 10
    new_codes = fresh.json()["codes"]
    stale = anonymous(app, config).post(
        "/api/account/reset", json={"method": "recovery_code", "secret": old_codes[0], "password": NEW}
    )
    assert stale.status_code == 401
    ok = anonymous(app, config).post(
        "/api/account/reset", json={"method": "recovery_code", "secret": new_codes[0], "password": NEW}
    )
    assert ok.status_code == 200
    assert (
        anonymous(app, config).post("/api/account/recovery-codes", json={"password": NEW}).status_code == 401
    )


def test_the_command_line_resets_the_password_without_echo_and_signs_everyone_out(
    app, config, capsys, monkeypatch
):
    session, _ = signed_up(app, config)
    answers = iter(["weak", "Fresh-Pass-123", "Different-Pass-1", "Fresh-Pass-123", "Fresh-Pass-123"])
    assert reset_password(config, ask=lambda prompt: next(answers)) == 0
    out = capsys.readouterr().out
    assert "Use at least 10 characters" in out and "differ" in out and "Password changed" in out
    assert "Fresh-Pass-123" not in out
    # Another process wrote the version; the server trusts its cached copy for a few seconds only.
    monkeypatch.setattr("jobpilot.api.sessions.VERSION_SECONDS", -1)
    assert session.get("/api/control").status_code == 401
    assert signed_in(app, config, password="Fresh-Pass-123")


def test_the_command_line_refuses_an_installation_without_a_password(config, capsys):
    assert reset_password(config, ask=lambda prompt: GOOD) == 1
    assert "access token" in capsys.readouterr().out


def test_onboarding_state_is_saved(client):
    state = client.put("/api/onboarding", json={"step": "authorization", "completed": False}).json()
    assert state["step"] == "authorization" and state["completed"] is False
    assert (
        client.put("/api/onboarding", json={"step": "review", "completed": True}).json()["completed"] is True
    )
    assert client.put("/api/onboarding", json={"step": "nonsense"}).status_code == 400
