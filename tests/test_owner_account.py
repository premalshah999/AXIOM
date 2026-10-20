"""Owner sign-up/sign-in (2026-10-15): anonymous sign-up only on a fresh installation; an installation
that holds data needs the access token first; email/password sessions then work like token sessions."""

from fastapi.testclient import TestClient

from jobpilot.owner_account import hash_password, password_problem, verify_password

GOOD = "Correct-Horse-42"


def anonymous(app, config):
    return TestClient(app, base_url=config.base_url)


def test_password_hash_round_trip_and_policy():
    stored = hash_password(GOOD)
    assert (
        stored.startswith("scrypt$") and verify_password(GOOD, stored) and not verify_password("nope", stored)
    )
    assert password_problem("short1A") and password_problem("alllowercase123") and not password_problem(GOOD)


def test_fresh_installation_signup_seeds_profile_and_starts_onboarding(app, config, service):
    web = anonymous(app, config)
    status = web.get("/api/account").json()
    assert status == {"exists": False, "signup_allowed": True, "signed_in": False}
    r = web.post(
        "/api/account/signup", json={"name": "Ada Lovelace", "email": "Ada@Example.com", "password": GOOD}
    )
    assert r.status_code == 200 and r.json()["onboarding"]["completed"] is False
    # The session cookie now opens the API.
    assert web.get("/api/control").status_code == 200
    profile = web.get("/api/profile").json()
    assert (profile["first_name"], profile["last_name"], profile["email"]) == (
        "Ada",
        "Lovelace",
        "ada@example.com",
    )
    assert "reviewed_answers" not in profile and "approved_answers" not in profile
    # A second account is never created.
    again = anonymous(app, config).post(
        "/api/account/signup", json={"name": "Eve", "email": "eve@x.io", "password": GOOD}
    )
    assert again.status_code == 409


def test_installation_with_data_refuses_anonymous_signup(app, config, client):
    client.put("/api/profile", json={"name": "Owner Person", "email": "owner@example.com"})
    web = anonymous(app, config)
    assert web.get("/api/account").json()["signup_allowed"] is False
    r = web.post("/api/account/signup", json={"name": "Visitor", "email": "v@x.io", "password": GOOD})
    assert r.status_code == 403
    # The signed-in owner (token) can add a password, then sign in with it.
    assert (
        client.post(
            "/api/account/signup",
            json={"name": "Owner Person", "email": "owner@example.com", "password": GOOD},
        ).status_code
        == 200
    )
    bad = web.post("/api/account/login", json={"email": "owner@example.com", "password": "wrong-Pass1"})
    assert bad.status_code == 401
    ok = web.post("/api/account/login", json={"email": "OWNER@example.com", "password": GOOD})
    assert ok.status_code == 200 and web.get("/api/control").status_code == 200


def test_owner_changes_password(app, config, client):
    client.post(
        "/api/account/signup", json={"name": "Owner Person", "email": "owner@example.com", "password": GOOD}
    )
    wrong = client.post("/api/account/password", json={"current": "Not-The-Pass1", "new": "Another-Pass-77"})
    assert wrong.status_code == 400 and "incorrect" in wrong.json()["detail"]
    weak = client.post("/api/account/password", json={"current": GOOD, "new": "short"})
    assert weak.status_code == 400
    assert client.post("/api/account/password", json={"current": GOOD, "new": "Another-Pass-77"}).json() == {
        "ok": True
    }
    web = anonymous(app, config)
    assert (
        web.post("/api/account/login", json={"email": "owner@example.com", "password": GOOD}).status_code
        == 401
    )
    assert (
        web.post(
            "/api/account/login", json={"email": "owner@example.com", "password": "Another-Pass-77"}
        ).status_code
        == 200
    )
    # Anonymous visitors cannot change it.
    assert (
        anonymous(app, config)
        .post("/api/account/password", json={"current": "Another-Pass-77", "new": "Third-Pass-999"})
        .status_code
        == 401
    )


def test_onboarding_state_is_saved(client):
    state = client.put("/api/onboarding", json={"step": "authorization", "completed": False}).json()
    assert state["step"] == "authorization" and state["completed"] is False
    assert (
        client.put("/api/onboarding", json={"step": "review", "completed": True}).json()["completed"] is True
    )
    assert client.put("/api/onboarding", json={"step": "nonsense"}).status_code == 400
