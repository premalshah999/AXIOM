"""Owner limits are editable from Setup › System instead of database edits."""


def test_limits_round_trip_and_bounds(client):
    r = client.get("/api/limits")
    assert r.status_code == 200 and {"job_token_cap", "company_limit"} <= set(r.json())
    r = client.put("/api/limits", json={"job_token_cap": 300000, "company_limit": 8})
    assert r.status_code == 200 and r.json() == {"job_token_cap": 300000, "company_limit": 8}
    assert client.get("/api/limits").json()["company_limit"] == 8
    assert client.put("/api/limits", json={"job_token_cap": 10, "company_limit": 8}).status_code == 422
