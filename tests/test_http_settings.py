"""HTTP contract of the settings routes: control switches, limits, model providers."""


def test_control_defaults_to_running_and_manual_submission(client):
    assert client.get("/api/control").json() == {"paused": False, "auto_submit": False}


def test_control_is_saved_and_echoed(client):
    answer = client.put("/api/control", json={"paused": True, "auto_submit": True})
    assert answer.status_code == 200 and answer.json() == {"paused": True, "auto_submit": True}
    assert client.get("/api/control").json() == {"paused": True, "auto_submit": True}


def test_control_requires_both_switches(client):
    assert client.put("/api/control", json={"paused": True}).status_code == 422


def test_limits_round_trip_within_their_bounds(client):
    answer = client.put("/api/limits", json={"job_token_cap": 600_000, "company_limit": 12})
    assert answer.json() == {"job_token_cap": 600_000, "company_limit": 12}
    assert client.get("/api/limits").json() == {"job_token_cap": 600_000, "company_limit": 12}
    assert client.put("/api/limits", json={"job_token_cap": 600_000, "company_limit": 26}).status_code == 422
    assert client.put("/api/limits", json={"job_token_cap": 49_999, "company_limit": 5}).status_code == 422


def test_agents_summary_never_contains_keys(client):
    body = client.get("/api/agents")
    assert body.status_code == 200
    assert "unit-test" not in body.text and "api_key" not in body.text.lower().replace("_configured", "")
