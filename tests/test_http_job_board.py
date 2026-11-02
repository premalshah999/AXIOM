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


def test_how_close_a_match_is_one_number_on_both_screens(client, service):
    """Settings › Applying and Profile › Job preferences both show "How close a match": saving either moves the other."""
    service.db.set_setting("profile", {"minimum_fit": 70})
    assert client.put("/api/job-board/settings", json={"minimum_auto_fit": 55}).status_code == 200
    assert service.db.get_setting("profile", {})["minimum_fit"] == 55
    # Saving the board form with the same number leaves a profile that was set apart alone.
    service.db.set_setting("profile", {"minimum_fit": 40})
    assert client.put("/api/job-board/settings", json={"minimum_auto_fit": 55, "interval_hours": 6}).status_code == 200
    assert service.db.get_setting("profile", {})["minimum_fit"] == 40


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
    assert client.post("/api/job-board/autopilot/start", json={"target": 1001}).status_code == 422


def test_a_new_install_with_roles_searches_without_being_asked(service):
    # The boards exist only after Search now or Start, so two friends who finished setup and waited saw nothing (2026-11-01).
    import asyncio

    from jobpilot.jobboard import first_search_due, managed_ids, request_scan

    db = service.db
    assert not first_search_due(db), "no roles, nothing to look for"
    db.set_setting("profile", {"target_roles": ["Data Analyst"]})
    assert first_search_due(db)
    queued = []

    async def enqueue(source_id):
        queued.append(source_id)
        return True

    ids, count = asyncio.run(request_scan(db, enqueue))
    assert ids and count == len(ids) == len(queued) == len(managed_ids(db))
    assert not first_search_due(db), "asked once; later searches follow the schedule or the button"
    assert db.get_setting("job_board_scan:" + queued[0], {})["state"] == "queued"
    again_ids, again = asyncio.run(request_scan(db, enqueue))
    assert again == 0 and again_ids == ids, "boards already queued are not queued twice"


def test_a_board_is_added_from_a_link_alone_and_a_posting_link_becomes_its_board(client, service):
    from jobpilot.db import Source

    posting = client.post("/api/job-board/sources/link", json={"url": "https://boards.greenhouse.io/example-co/jobs/123?gh_src=x"})
    assert posting.status_code == 200, posting.text
    assert (posting.json()["name"], posting.json()["kind"], posting.json()["url"]) == ("Example Co", "greenhouse", "https://boards.greenhouse.io/example-co")
    with service.db.session() as s:
        assert s.get(Source, posting.json()["id"]).url == "https://boards.greenhouse.io/example-co"
    again = client.post("/api/job-board/sources/link", json={"url": "boards.greenhouse.io/example-co"})
    assert again.json()["id"] == posting.json()["id"]  # the same board, however it was pasted
    assert client.post("/api/job-board/sources/link", json={"url": "https://boards.greenhouse.io"}).status_code == 422  # no company named
    assert client.post("/api/job-board/sources/link", json={"url": "ftp://example.test"}).status_code == 422
