"""HTTP contract of the health routes: /healthz is public and minimal, /api/runtime-health needs a sign-in."""

from fastapi.testclient import TestClient


def test_healthz_is_public(app):
    answer = TestClient(app).get("/healthz")
    assert answer.status_code == 200
    assert answer.json() == {"status": "ok", "version": "1.0.0"}


def test_runtime_health_reports_the_engine_and_the_outbox(client):
    body = client.get("/api/runtime-health").json()
    assert {"engine_version", "host", "scheduler", "heartbeat", "outbox_pending", "outbox_dead"} <= set(body)
    assert {"workers", "application_workers", "models", "production_target"} <= set(body)
    assert body["outbox_pending"] == 0 and body["outbox_dead"] == 0


def test_runtime_health_needs_a_sign_in(app):
    assert TestClient(app).get("/api/runtime-health").status_code == 401
