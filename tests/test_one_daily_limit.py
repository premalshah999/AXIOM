"""The daily limit is one value (owner, 2026-11-02: "set the maximum limit of applications a day through settings and dashboard, but
sync the values"): Settings › Limits, the Overview's number beside Start, the batch's goal and the engine's cap. Synthetic data only."""

import pytest

from jobpilot import autopilot

pytestmark = pytest.mark.integration


def test_saving_the_limit_changes_the_engine_the_batch_goal_and_the_snapshot(client, service):
    autopilot.start_batch(service.db, 100)
    assert client.put("/api/connections", json={"values": {"daily_application_limit": "37"}}).status_code == 200
    assert service.config.daily_application_limit == 37  # the engine reads this on every start (service/execution.py, autopilot.tick)
    assert service.db.get_setting("job_board_autopilot")["target"] == 37
    assert service.db.get_setting("job_board_settings")["batch_target"] == 37
    assert client.get("/api/snapshot").json()["config"]["daily_limit"] == 37
    # Lowered again from the other screen: the running batch follows down as well as up.
    assert client.put("/api/connections", json={"values": {"daily_application_limit": "12"}}).status_code == 200
    assert service.db.get_setting("job_board_autopilot")["target"] == 12


def test_the_dashboard_number_beside_start_is_the_same_setting(client, service, monkeypatch):
    async def started(service, target):
        return {"batch": autopilot.start_batch(service.db, target)}

    monkeypatch.setattr(autopilot, "start_autonomous", started)
    monkeypatch.setattr(autopilot, "batch_snapshot", lambda service: {})
    service.dispatch = object()
    started = client.post("/api/job-board/autopilot/start", json={"target": 55})
    assert started.status_code == 200, started.text
    assert service.config.daily_application_limit == 55
    values = {f["name"]: f for f in client.get("/api/connections").json()["fields"]}
    assert str(values["daily_application_limit"]["value"]) == "55"


def test_the_discovery_form_cannot_set_a_second_goal(client, service):
    client.put("/api/connections", json={"values": {"daily_application_limit": "40"}})
    board = client.get("/api/job-board/settings").json() if client.get("/api/job-board/settings").status_code == 200 else {}
    body = {**board, "batch_target": 99}
    body = {k: v for k, v in body.items() if k in {"enabled", "auto_apply", "batch_target", "minimum_auto_fit", "max_age_hours", "early_career_only", "review_size"}}
    client.put("/api/job-board/settings", json=body)
    assert service.db.get_setting("job_board_settings")["batch_target"] == 40


def test_start_turns_on_account_and_terms_permissions_only_when_the_dialog_allows_it(client, service, monkeypatch):
    from jobpilot import apply_permissions
    from jobpilot.schemas import Profile

    async def started(service, target):
        return {"batch": autopilot.start_batch(service.db, target)}

    monkeypatch.setattr(autopilot, "start_autonomous", started)
    monkeypatch.setattr(autopilot, "batch_snapshot", lambda service: {})
    service.dispatch = object()
    service.db.set_setting("profile", {"first_name": "Alex"})
    flags = lambda: apply_permissions.missing(Profile.model_validate(service.db.get_setting("profile", {})))  # noqa: E731
    assert len(flags()) == 3
    for body in ({"target": 20}, {"target": 20, "permissions": "decline"}):
        assert client.post("/api/job-board/autopilot/start", json=body).status_code == 200
        assert len(flags()) == 3  # never silently, and an explicit No keeps them off
    assert client.post("/api/job-board/autopilot/start", json={"target": 20, "permissions": "allow"}).status_code == 200
    assert flags() == []
    assert service.db.get_setting("profile")["first_name"] == "Alex"
