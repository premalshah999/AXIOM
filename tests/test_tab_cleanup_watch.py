"""The tab cleanup runs inside the app (it used to be a second container running a second copy of the code)."""

import asyncio

from jobpilot.operations import close_tabs


async def test_watch_records_health_and_keeps_going_when_chrome_is_down(service, monkeypatch):
    calls = []

    def sweep(db, endpoint, client):
        calls.append(endpoint)
        if len(calls) == 1:
            raise ConnectionError("Chrome is not running yet")
        return {"closed": 2, "pending": 0}

    waits = []

    async def stop_after_two_sweeps(seconds):
        waits.append(seconds)
        if len(waits) == 2:
            raise asyncio.CancelledError

    monkeypatch.setattr(close_tabs, "close_inactive_tabs", sweep)
    try:
        await close_tabs.watch(
            service.db, "http://host.docker.internal:9224", 15, sleep=stop_after_two_sweeps
        )
    except asyncio.CancelledError:
        pass
    assert calls == ["http://host.docker.internal:9224"] * 2
    assert waits == [15, 15]
    health = service.db.get_setting("inactive_tab_cleanup")
    assert health["state"] == "healthy" and health["closed"] == 2


async def test_watch_without_a_desktop_chrome_does_nothing(service):
    # No BROWSER_CDP_URL: nothing to sweep, and no setting is written.
    await close_tabs.watch(service.db, "", 15)
    assert service.db.get_setting("inactive_tab_cleanup") is None


async def test_unreachable_chrome_is_reported_not_raised(service, monkeypatch):
    def down(db, endpoint, client):
        raise OSError("connection refused")

    async def once(seconds):
        raise asyncio.CancelledError

    monkeypatch.setattr(close_tabs, "close_inactive_tabs", down)
    try:
        await close_tabs.watch(service.db, "http://127.0.0.1:9224", 15, sleep=once)
    except asyncio.CancelledError:
        pass
    health = service.db.get_setting("inactive_tab_cleanup")
    assert health["state"] == "unavailable" and health["error_type"] == "OSError"


def test_the_sweep_reads_only_target_fields_and_takes_no_lock_when_nothing_is_open(service, monkeypatch):
    """Each desktop_job value also holds the run's ledger and checkpoints (22.6 MB over 1,562 rows in production).
    Loading them all every 15 s inside the app starved its threads and exhausted the connection pool (2026-10-20)."""
    import httpx
    from sqlalchemy import event

    from jobpilot.db import Run, Setting
    from jobpilot.operations import close_tabs

    with service.db.session() as s:
        s.add_all(
            [
                Run(id="done", job_id="job", state="confirmed"),
                Setting(key="desktop_job:job", value={"target_id": None, "ledger": {"field": "x" * 200_000}}),
            ]
        )
    monkeypatch.setattr(close_tabs, "_targets", lambda endpoint, client: {})
    statements = []

    def record(conn, cursor, statement, *args):
        statements.append(statement)

    event.listen(service.db.engine, "before_cursor_execute", record)
    try:
        with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=[]))) as client:
            assert close_tabs.close_inactive_tabs(service.db, "http://chrome.test", client) == {"closed": 0, "pending": 0}
    finally:
        event.remove(service.db.engine, "before_cursor_execute", record)
    joined = "\n".join(statements)
    assert "BEGIN IMMEDIATE" not in joined and "pg_advisory_xact_lock" not in joined
    assert "settings.value AS settings_value" not in joined
    assert service.db.get_setting("desktop_job:job")["ledger"]["field"] == "x" * 200_000
