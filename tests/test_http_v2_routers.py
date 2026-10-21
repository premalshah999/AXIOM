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
