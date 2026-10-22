"""Setup → Accounts (2026-10-22): each employer account in plain words, the owner's own password for an employer,
and retrying a stuck email verification. No response ever carries a stored password."""

import json
from types import SimpleNamespace

from jobpilot.accounts import Accounts, AccountStore
from jobpilot.db import Job, Run

from support_http import seed_job

EMAIL = "applicant@example.com"
OLD = "Shared-Pass-11"
NEW = "Owner-Reset-22"
JOB = "https://careers.acme.example.test/jobs/1"


def record(service, url=JOB, state="authenticated", job_url=JOB, **extra):
    store = AccountStore(service, EMAIL, job_url)
    store.save(url, {"email": EMAIL, "password": OLD}, state, **extra)
    return store, store.key(url)


def listed(client):
    answer = client.get("/api/accounts")
    assert answer.status_code == 200
    # Neither a password nor a sealed value (Fernet tokens start with gAAAAA) nor a credentials field.
    assert OLD not in answer.text and NEW not in answer.text and "gAAAAA" not in answer.text
    rows = answer.json()["accounts"]
    assert not any(name.endswith("credentials") or name == "security_answers" for row in rows for name in row)
    return {a["id"]: a for a in rows}


def test_each_engine_state_is_shown_in_plain_words_without_any_password(client, service):
    states = {
        "authenticated": ("active", "Active"),
        "password_reset": ("active", "Active"),
        "verification_pending": ("verification", "Needs email verification"),
        "credentials_rejected": ("password_unknown", "Password unknown"),
        "password_unknown": ("password_unknown", "Password unknown"),
        "reset_requested": ("reset_pending", "Reset pending"),
        "locked": ("locked", "Locked"),
        "created_locally": ("created", "Created"),
        "exists": ("registered", "Already registered"),
    }
    keys = {}
    for n, state in enumerate(states):
        url = f"https://careers{n}.example.test/apply"
        keys[state] = record(service, url, state, job_url=url)[1]
    rows = listed(client)
    for state, (status, label) in states.items():
        row = rows[keys[state]]
        assert (row["status"], row["label"]) == (status, label), state
        assert row["login_email"] == EMAIL and row["detail"] and row["updated_at"]
    assert rows[keys["password_unknown"]]["attention"] and not rows[keys["authenticated"]]["attention"]
    assert "save the new password here" in rows[keys["credentials_rejected"]]["detail"]
    # Accounts that need the owner come first.
    order = [a["status"] for a in client.get("/api/accounts").json()["accounts"]]
    first_calm = next(
        i
        for i, s in enumerate(order)
        if s not in {"verification", "password_unknown", "reset_pending", "locked"}
    )
    assert all(
        s not in {"verification", "password_unknown", "reset_pending", "locked"} for s in order[first_calm:]
    )


def test_a_shared_ats_host_names_its_employer(client, service):
    _, key = record(
        service,
        "https://login.icims.com/signin",
        job_url="https://careers-acme.icims.com/jobs/1/job",
    )
    row = listed(client)[key]
    assert row["host"] == "login.icims.com" and row["employer"] == "careers-acme.icims.com"


def test_i_reset_it_myself_saves_a_sealed_password_the_engine_prefers(client, service):
    store, key = record(
        service,
        state="credentials_rejected",
        rejected_credentials="fingerprint",
        rejected_at="2026-10-21T10:00:00+00:00",
        reset_requested_at=1_760_000_000.0,
        error="Too many attempts",
    )
    store.remember(JOB, security_answers={"pet": "Rex"})
    answer = client.put(f"/api/accounts/{key}/password", json={"password": NEW})
    assert answer.status_code == 200 and NEW not in answer.text and OLD not in answer.text
    row = answer.json()["account"]
    assert (row["status"], row["label"], row["owner_set_at"] is not None) == ("active", "Active", True)
    assert store.get(JOB) == {"email": EMAIL, "password": NEW}
    saved = service.db.get_setting(key)
    assert saved["state"] == "password_reset" and saved["confirmed_at"]
    assert not {"rejected_credentials", "rejected_at", "reset_requested_at", "error"} & set(saved)
    assert service.mail.vault.open(saved["security_answers"]) == {"pet": "Rex"}
    assert NEW not in json.dumps(saved)
    # The next run signs in with it instead of the shared password.
    profile = SimpleNamespace(email=EMAIL, allow_account_creation=True)
    assert Accounts(service, profile, JOB).credentials(JOB) == {"email": EMAIL, "password": NEW}
    assert NEW not in client.get("/api/accounts").text


def test_saving_an_employer_password_is_refused_plainly(client, service):
    _, key = record(service)
    empty = client.put(f"/api/accounts/{key}/password", json={"password": "   "})
    assert empty.status_code == 400 and "new password" in empty.json()["detail"]
    missing = client.put("/api/accounts/employer_account:unknown/password", json={"password": NEW})
    assert missing.status_code == 404
    assert client.put("/api/accounts/profile/password", json={"password": NEW}).status_code == 404
    service.db.set_setting(
        "employer_account:sealless", {"origin": "https://x.example.test", "state": "locked"}
    )
    sealless = client.put("/api/accounts/employer_account:sealless/password", json={"password": NEW})
    assert sealless.status_code == 400 and "Forget it instead" in sealless.json()["detail"]


def held_verification(service, url=JOB, *, challenge="challenge-1"):
    job_id = seed_job(service)
    with service.db.exclusive() as s:
        s.get(Job, job_id).url = url
        run = Run(
            job_id=job_id,
            state="submission_unknown",
            mode="submit",
            packet={"wait_handle": {"challenge_id": challenge}},
        )
        s.add(run)
        s.flush()
        return job_id, run.id


def test_retry_verification_restarts_the_paused_draft_of_that_employer(client, service, monkeypatch):
    _, key = record(service, state="verification_pending")
    calls = []

    async def resume(run_id, challenge_id, *, restart_verification=False):
        calls.append((run_id, challenge_id, restart_verification))
        return {"run_id": "child-run", "state": "queued", "verification_only": True}

    monkeypatch.setattr(service, "resume_mail_wait", resume)
    nothing = client.post(f"/api/accounts/{key}/verify", json={})
    assert nothing.status_code == 409 and "No paused application" in nothing.json()["detail"]
    assert listed(client)[key]["can_retry_verification"] is False
    _, run_id = held_verification(service)
    assert listed(client)[key]["can_retry_verification"] is True
    answer = client.post(f"/api/accounts/{key}/verify", json={})
    assert answer.status_code == 200 and answer.json() == {
        "ok": True,
        "run_id": "child-run",
        "state": "queued",
    }
    assert calls == [(run_id, "challenge-1", True)]


def test_retry_verification_only_for_accounts_waiting_on_it(client, service, monkeypatch):
    _, key = record(service, state="authenticated")
    held_verification(service)
    assert listed(client)[key]["can_retry_verification"] is False
    answer = client.post(f"/api/accounts/{key}/verify", json={})
    assert answer.status_code == 409 and "waiting for email verification" in answer.json()["detail"]
    assert client.post("/api/accounts/employer_account:gone/verify", json={}).status_code == 404


def test_a_draft_the_service_no_longer_accepts_is_explained(client, service, monkeypatch):
    _, key = record(service, state="verification_pending")
    held_verification(service)

    async def refuse(run_id, challenge_id, *, restart_verification=False):
        raise ValueError("A retained, uncertain verification draft is required")

    monkeypatch.setattr(service, "resume_mail_wait", refuse)
    answer = client.post(f"/api/accounts/{key}/verify", json={})
    assert answer.status_code == 409 and "can no longer retry" in answer.json()["detail"]


def test_a_newer_run_of_the_same_job_is_not_offered_for_retry(client, service):
    _, key = record(service, state="verification_pending")
    job_id, _ = held_verification(service)
    with service.db.exclusive() as s:
        s.add(Run(job_id=job_id, state="failed", mode="submit", created_at="2999-01-01T00:00:00+00:00"))
    assert listed(client)[key]["can_retry_verification"] is False
