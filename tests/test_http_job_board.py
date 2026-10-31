"""HTTP contract of the job-board routes: snapshot, settings, managed sources, scans, selection, unattended batch."""

from jobpilot.db import Source

SOURCE = {"name": "Acme Example", "kind": "greenhouse", "url": "https://boards.example.test/acme"}


def test_the_board_snapshot_has_settings_sources_jobs_and_the_search_provider(client):
    board = client.get("/api/job-board").json()
    assert {"jobs", "sources", "source_count", "search"} <= set(board)
    assert board["search"] == {
        "google_configured": False,
        "provider": "Free web search (DuckDuckGo / Bing)",
    }


def test_the_board_snapshot_can_leave_the_sources_out(client):
    client.post("/api/job-board/sources", json=SOURCE)
    full = client.get("/api/job-board").json()
    lean = client.get("/api/job-board", params={"include_sources": "false"}).json()
    assert len(full["sources"]) == 1 and lean["sources"] == [] and lean["source_count"] == 1


def test_a_managed_source_is_added_once_and_follows_the_board_schedule(client, service):
    added = client.post("/api/job-board/sources", json=SOURCE)
    assert added.status_code == 200 and added.json()["added"] is True
    again = client.post("/api/job-board/sources", json=SOURCE)
    assert again.status_code == 200 and again.json()["id"] == added.json()["id"]
    with service.db.session() as s:
        row = s.get(Source, added.json()["id"])
        assert row.auto_queue is False and row.cron.startswith("0 */")
    assert added.json()["id"] in service.db.get_setting("job_board_sources")["ids"]


def test_a_plain_source_cannot_be_adopted_as_a_managed_one(client):
    assert client.post("/api/sources", json=SOURCE).status_code == 200
    answer = client.post("/api/job-board/sources", json=SOURCE)
    assert answer.status_code == 409 and "already configured in Sources" in answer.json()["detail"]


def test_board_settings_are_saved_and_visible_in_the_snapshot(client):
    saved = client.put(
        "/api/job-board/settings", json={"enabled": True, "interval_hours": 6, "early_career_only": True}
    )
    assert saved.status_code == 200 and saved.json()["saved"] is True
    settings = client.get("/api/snapshot", params={"include_sources": "false"}).json()["discovery_settings"]
    assert settings["interval_hours"] == 6 and settings["early_career_only"] is True


def test_board_settings_reject_an_interval_outside_the_choices(client):
    assert client.put("/api/job-board/settings", json={"interval_hours": 5}).status_code == 422


def test_scan_and_batch_need_workers(client):
    for call in (
        client.post("/api/job-board/scrape", json={}),
        client.post("/api/job-board/autopilot/start", json={"target": 10}),
    ):
        assert call.status_code == 409, call.text


def test_the_batch_target_is_bounded(client):
    assert client.post("/api/job-board/autopilot/start", json={"target": 0}).status_code == 422
    assert client.post("/api/job-board/autopilot/start", json={"target": 501}).status_code == 422
