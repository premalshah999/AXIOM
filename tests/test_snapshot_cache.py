"""Dashboard snapshot cache (2026-10-15): polls are answered from the last build while a background
thread refreshes it; any write through the API makes the next read rebuild it."""

import time

from fastapi.testclient import TestClient

from jobpilot.api import create_app


def cached_client(config, seconds):
    config.snapshot_cache_seconds = seconds
    app = create_app(config)
    return app.state.service, TestClient(app, headers={"Authorization": "Bearer " + config.app_token})


def health(client):
    return client.get("/api/snapshot?include_sources=false").json()["telegram_health"]


def test_polls_reuse_the_last_build_until_a_write(config):
    service, client = cached_client(config, 30)
    service.db.set_setting("telegram_health", {"state": "first"})
    assert health(client) == {"state": "first"}
    # A change made outside the API (a worker, Telegram) is picked up by the next refresh, not instantly.
    service.db.set_setting("telegram_health", {"state": "second"})
    assert health(client) == {"state": "first"}
    # A write through the API marks the snapshot stale, so the next read is fresh.
    assert client.put("/api/control", json={"paused": False, "auto_submit": False}).status_code == 200
    assert health(client) == {"state": "second"}


def test_an_old_build_is_served_once_while_it_refreshes(config):
    service, client = cached_client(config, 0.05)
    service.db.set_setting("telegram_health", {"state": "first"})
    assert health(client) == {"state": "first"}
    service.db.set_setting("telegram_health", {"state": "second"})
    time.sleep(0.1)
    assert health(client) == {"state": "first"}  # answered immediately from the old build
    for _ in range(50):  # the background refresh lands shortly after
        time.sleep(0.05)
        if health(client) == {"state": "second"}:
            break
    assert health(client) == {"state": "second"}


def test_snapshot_omits_batch_run_ids(client, service):
    service.db.set_setting("job_board_autopilot", {"state": "active", "target": 100, "run_ids": ["r1", "r2"]})
    autopilot = client.get("/api/snapshot?include_sources=false").json()["autopilot"]
    assert autopilot == {"state": "active", "target": 100}
