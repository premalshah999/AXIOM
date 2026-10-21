"""HTTP contract of the legacy reviewed-answer routes, the employer account records and imported browser sessions."""

import json

from jobpilot.db import Setting

ENTRY = {"question": "Are you willing to work onsite?", "answer": "Yes"}


def test_legacy_knowledge_save_search_and_forget(client):
    saved = client.post("/api/knowledge", json=ENTRY)
    assert saved.status_code == 200
    entry = saved.json()
    assert entry["question"] == ENTRY["question"] and entry["answer"] == "Yes"
    found = client.get("/api/knowledge", params={"q": "onsite"}).json()["entries"]
    assert [e["id"] for e in found] == [entry["id"]]
    assert client.get("/api/knowledge", params={"q": "zebra"}).json() == {"entries": []}
    assert client.delete(f"/api/knowledge/{entry['id']}").json() == {"deleted": True}
    assert client.delete(f"/api/knowledge/{entry['id']}").status_code == 400
    assert client.get("/api/knowledge").json() == {"entries": []}


def test_legacy_knowledge_rejects_an_empty_answer(client):
    assert (
        client.post("/api/knowledge", json={"question": "Anything at all?", "answer": ""}).status_code == 422
    )


def test_accounts_list_records_without_credentials_and_forget_one(client, service):
    key = "employer_account:acme.example.test"
    service.db.set_setting(
        key,
        {
            "origin": "https://acme.example.test",
            "tenant": "acme",
            "state": "created",
            "password": "secret-value",
        },
    )
    body = client.get("/api/accounts")
    assert body.status_code == 200 and "secret-value" not in body.text
    assert [a["id"] for a in body.json()["accounts"]] == [key]
    assert body.json()["password_problems"] == []
    assert client.delete(f"/api/accounts/{key}").json() == {"deleted": True}
    assert client.get("/api/accounts").json()["accounts"] == []
    assert client.delete(f"/api/accounts/{key}").status_code == 404


def test_only_employer_account_records_can_be_forgotten(client, service):
    service.db.set_setting("profile", {"name": "Alex Example"})
    assert client.delete("/api/accounts/profile").status_code == 404
    with service.db.session() as s:
        assert s.get(Setting, "profile") is not None


def storage_state(domain="acme.example.test"):
    return json.dumps(
        {"cookies": [{"name": "sid", "value": "x", "domain": domain, "path": "/"}], "origins": []}
    )


def test_a_session_for_the_employer_host_is_saved_with_owner_only_permissions(client, config):
    answer = client.post(
        "/api/sessions",
        data={"url": "https://careers.acme.example.test/jobs/1"},
        files={"file": ("state.json", storage_state(), "application/json")},
    )
    assert answer.status_code == 200 and answer.json() == {"saved": True, "host": "careers.acme.example.test"}
    files = list((config.data_dir / "sessions").glob("*.json"))
    assert len(files) == 1 and files[0].stat().st_mode & 0o777 == 0o600


def test_a_session_with_cookies_of_another_site_is_refused(client, config):
    answer = client.post(
        "/api/sessions",
        data={"url": "https://careers.acme.example.test/jobs/1"},
        files={"file": ("state.json", storage_state("other.example.test"), "application/json")},
    )
    assert answer.status_code == 400 and "unrelated cookies" in answer.json()["detail"]
    assert not list((config.data_dir / "sessions").glob("*.json"))


def test_a_session_file_that_is_not_json_is_refused(client):
    answer = client.post(
        "/api/sessions",
        data={"url": "https://careers.acme.example.test/jobs/1"},
        files={"file": ("state.json", b"not json", "application/json")},
    )
    assert answer.status_code == 400


def test_onboarding_defaults_to_the_first_step_and_saves_progress(client):
    first = client.get("/api/onboarding").json()
    assert first["step"] == "resume" and first["completed"] is False
    saved = client.put("/api/onboarding", json={"step": "contact", "completed": False})
    assert saved.status_code == 200 and saved.json()["step"] == "contact"
    assert client.put("/api/onboarding", json={"step": "nonsense", "completed": False}).status_code == 400
