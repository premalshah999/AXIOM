"""HTTP contract of the synthetic employer form (`jobpilot verify`) and of the dashboard fallback."""

from fastapi.testclient import TestClient


def test_the_demo_is_refused_when_workers_are_disabled(client):
    answer = client.post("/api/demo", json={"mode": "dry_run"})
    assert answer.status_code == 400 and answer.json() == {"detail": "Demo or workers are disabled"}


def test_the_demo_form_exists_only_for_a_valid_nonce(app):
    web = TestClient(app)  # the form is public: the nonce is the credential
    assert web.get("/demo/form/not-a-nonce").status_code == 404
    assert web.post("/demo/form/not-a-nonce", data={"first_name": "Alex"}).status_code == 404


def test_the_dashboard_fallback_serves_a_page_not_json(app):
    web = TestClient(app)
    answer = web.get("/some/dashboard/route")
    assert answer.status_code in {200, 503}
    assert answer.headers["content-type"].startswith("text/html")
    assert web.get("/").headers["content-type"].startswith("text/html")


def test_unknown_api_paths_never_fall_through_to_the_dashboard(app, config):
    web = TestClient(app, headers={"Authorization": "Bearer " + config.app_token})
    answer = web.get("/api/nothing/here")
    assert answer.status_code == 404 and answer.headers["content-type"].startswith("application/json")
