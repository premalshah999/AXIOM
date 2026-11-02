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


def test_no_dont_make_accounts_turns_off_every_permission_it_declines(client, service, monkeypatch):
    from jobpilot import apply_permissions
    from jobpilot.schemas import Profile

    async def started(service, target):
        return {"batch": autopilot.start_batch(service.db, target)}

    monkeypatch.setattr(autopilot, "start_autonomous", started)
    monkeypatch.setattr(autopilot, "batch_snapshot", lambda service: {})
    service.dispatch = object()
    on = lambda: {n: service.db.get_setting("profile")[n] for n in apply_permissions.FLAGS}  # noqa: E731
    mixed = {"first_name": "Alex", "allow_account_creation": True, "accept_all_application_terms": False, "allow_application_consents": True}
    service.db.set_setting("profile", mixed)
    # Not yet is the dialog closing: it never calls the route, and a start without an answer changes nothing.
    assert client.post("/api/job-board/autopilot/start", json={"target": 20}).status_code == 200
    assert service.db.get_setting("profile") == mixed
    for before in (mixed, {n: True for n in apply_permissions.FLAGS}, {n: False for n in apply_permissions.FLAGS}):
        service.db.set_setting("profile", {"first_name": "Alex", **before})
        assert client.post("/api/job-board/autopilot/start", json={"target": 20, "permissions": "decline"}).status_code == 200
        assert on() == {n: False for n in apply_permissions.FLAGS}
        assert not Profile.model_validate(service.db.get_setting("profile")).allow_account_creation
        assert service.db.get_setting("profile")["first_name"] == "Alex"


def test_a_limit_out_of_range_is_refused_in_plain_words():
    from jobpilot.connections import typed

    assert typed("daily_application_limit", 1000) == 1000
    with pytest.raises(ValueError) as too_many:
        typed("daily_application_limit", 1001)
    assert str(too_many.value) == "Applications per day must be 1000 or less"
    with pytest.raises(ValueError) as too_few:
        typed("daily_application_limit", 0)
    assert str(too_few.value) == "Applications per day must be 1 or more"
    with pytest.raises(ValueError) as not_a_number:
        typed("daily_application_limit", "many")
    assert str(not_a_number.value) == "Applications per day isn't a valid value"
