"""HTTP contract of the job-source routes."""

from jobpilot.db import Source

SOURCE = {"name": "Acme Example", "kind": "greenhouse", "url": "https://boards.example.test/acme"}


def test_add_edit_and_delete_a_source(client, service):
    added = client.post("/api/sources", json=SOURCE)
    assert added.status_code == 200
    source = added.json()
    assert source["name"] == "Acme Example" and source["enabled"] is True and source["auto_queue"] is False
    edited = client.put(
        f"/api/sources/{source['id']}", json={**SOURCE, "name": "Acme Careers", "enabled": False}
    )
    assert edited.json() == {"saved": True}
    with service.db.session() as s:
        row = s.get(Source, source["id"])
        assert (row.name, row.enabled) == ("Acme Careers", False)
    assert client.delete(f"/api/sources/{source['id']}").json() == {"deleted": True}
    with service.db.session() as s:
        assert s.get(Source, source["id"]) is None
    # Deleting a source that is already gone is not an error.
    assert client.delete(f"/api/sources/{source['id']}").json() == {"deleted": True}


def test_a_source_needs_a_known_kind_and_a_valid_schedule(client):
    assert client.post("/api/sources", json={**SOURCE, "kind": "myspace"}).status_code == 422
    assert client.post("/api/sources", json={**SOURCE, "cron": "not a schedule"}).status_code == 422


def test_editing_an_unknown_source_is_404(client):
    assert client.put("/api/sources/unknown", json=SOURCE).status_code == 404


def test_scan_now_needs_a_known_source_and_workers(client):
    assert client.post("/api/sources/unknown/discover", json={}).status_code == 404
    source = client.post("/api/sources", json=SOURCE).json()
    answer = client.post(f"/api/sources/{source['id']}/discover", json={})
    assert answer.status_code == 400 and answer.json() == {"detail": "Workers are disabled"}
