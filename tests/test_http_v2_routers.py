"""HTTP smoke test of the three /api/v2 feature routers: each answers behind the same sign-in and error rules."""

from fastapi.testclient import TestClient


def test_the_v2_routers_need_a_sign_in(app):
    web = TestClient(app)
    for path in ("/api/v2/knowledge/summary", "/api/v2/resumes/master", "/api/v2/discovery/status"):
        assert web.get(path).status_code == 401, path


def test_knowledge_summary_and_facts_are_paged(client):
    summary = client.get("/api/v2/knowledge/summary")
    assert summary.status_code == 200 and "store_version" in summary.json()
    facts = client.get("/api/v2/knowledge/facts", params={"size": 5}).json()
    assert {"items", "total", "page", "size", "store_version"} <= set(facts)


def test_the_master_resume_starts_unapproved(client):
    answer = client.get("/api/v2/resumes/master")
    assert answer.status_code == 200 and isinstance(answer.json(), dict)


def test_discovery_status_and_feed_state(client):
    assert client.get("/api/v2/discovery/status").status_code == 200
    assert client.get("/api/v2/discovery/feeds").status_code == 200


def test_a_confirmed_answer_is_saved_listed_and_retired(client):
    saved = client.post(
        "/api/v2/knowledge/facts", json={"question": "Are you willing to work onsite?", "value": "Yes"}
    )
    assert saved.status_code == 200, saved.text
    listed = client.get("/api/v2/knowledge/facts", params={"q": "onsite"}).json()
    assert listed["total"] >= 1
    fact = next(i for i in listed["items"] if "onsite" in i["question"].lower())
    retired = client.post(f"/api/v2/knowledge/facts/{fact['id']}/retire", json={})
    assert retired.status_code == 200 and "store_version" in retired.json()


def test_an_unknown_attestation_is_a_422(client):
    assert (
        client.post("/api/v2/knowledge/attestations/unknown", json={"statement": "I confirm"}).status_code
        == 422
    )
    assert "items" in client.get("/api/v2/knowledge/attestations").json()


def test_knowledge_employers_are_paged(client):
    page = client.get("/api/v2/knowledge/employers").json()
    assert {"items", "total", "page", "size"} <= set(page)


def test_the_discovery_diagnostics_answer(client):
    assert client.get("/api/v2/discovery/due").status_code == 200
    assert client.get("/api/v2/discovery/ready").status_code == 200
    assert client.get("/api/v2/discovery/requisitions").status_code == 200
    assert client.get("/api/v2/discovery/sources/health").status_code == 200


def test_resume_reads_that_need_no_master_answer(client):
    assert client.get("/api/v2/resumes/evidence").status_code == 200
    assert client.get("/api/v2/resumes/editor/templates").status_code == 200
    assert client.get("/api/v2/resumes/artifacts").status_code == 200
    assert client.get("/api/v2/resumes/artifacts/unknown").status_code in {404, 422}


def test_knowledge_migration_can_be_previewed_and_retrieval_needs_a_question(client):
    assert client.post("/api/v2/knowledge/migrate", params={"dry_run": "true"}, json={}).status_code == 200
    assert client.post("/api/v2/knowledge/retrieve", json={"question": ""}).status_code == 422
