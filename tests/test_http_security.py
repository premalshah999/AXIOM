"""HTTP contract of the request rules every /api route shares: sign-in, Origin, content type, headers, error mapping."""

from fastapi.testclient import TestClient

from support_http import seed_job


def test_an_api_call_without_credentials_is_401(app):
    answer = TestClient(app).get("/api/snapshot")
    assert answer.status_code == 401
    assert answer.json() == {"detail": "Sign in with your local access token"}


def test_a_foreign_origin_is_403_even_with_the_token(app, config):
    web = TestClient(app, headers={"Authorization": "Bearer " + config.app_token})
    answer = web.get("/api/control", headers={"Origin": "https://evil.example.test"})
    assert answer.status_code == 403
    assert web.get("/api/control", headers={"Origin": config.base_url}).status_code == 200


def test_a_write_must_be_json_or_multipart(client):
    answer = client.post("/api/runs/anything/reconcile", content="x", headers={"Content-Type": "text/plain"})
    assert answer.status_code == 415


def test_api_responses_are_not_cached_and_every_response_carries_the_hardening_headers(client):
    answer = client.get("/api/control")
    assert answer.headers["cache-control"] == "no-store"
    assert answer.headers["x-content-type-options"] == "nosniff"
    assert answer.headers["x-frame-options"] == "DENY"
    assert answer.headers["referrer-policy"] == "no-referrer"
    assert "cache-control" not in client.get("/healthz").headers


def test_a_value_error_in_a_handler_is_a_400_with_the_message(client):
    answer = client.post("/api/runs/unknown/cancel", json={})
    assert answer.status_code == 400
    assert "Only queued runs can be cancelled" in answer.json()["detail"]


def test_a_schema_error_is_a_422(client):
    assert client.put("/api/limits", json={"job_token_cap": 1, "company_limit": 1}).status_code == 422


def test_unknown_api_paths_are_404_and_not_the_dashboard(client, service):
    assert client.get("/api/does-not-exist").status_code == 404
    assert client.get("/demo/other").status_code == 404
    seed_job(service)  # nothing about the catch-all depends on data
    assert client.get("/api/jobs/unknown-id").status_code == 404
