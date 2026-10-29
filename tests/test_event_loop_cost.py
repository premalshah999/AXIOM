"""Database work the dashboard's writes put on the event loop (2026-10-29).

One asyncio loop drives the API, the queues and every browser owner. A handler that is `async def` runs its database
calls on that loop; a write also waits for the per-app advisory lock, which another thread can hold for seconds.
"""

import inspect
import threading

import pytest

from jobpilot.db import Job

# Handlers that only read or write the database. As `async def` they ran on the event loop, and a write waits for the
# per-app advisory lock another thread may hold while it decodes packets: the loop, and every browser, waited too.
THREADED = {
    ("DELETE", "/api/accounts/{account_id}"),
    ("PUT", "/api/job-board/settings"),
    ("POST", "/api/knowledge"),
    ("DELETE", "/api/knowledge/{entry_id}"),
    ("GET", "/api/mail"),
    ("DELETE", "/api/mail-rules/{rule_id}"),
    ("POST", "/api/mail-challenges/{challenge_id}/cancel"),
    ("PUT", "/api/profile"),
    ("DELETE", "/api/resumes/{resume_id}"),
    ("POST", "/api/runs/{run_id}/cancel"),
    ("POST", "/api/runs/{run_id}/reconcile"),
    ("PUT", "/api/agents"),
    ("DELETE", "/api/sources/{source_id}"),
}


def test_database_only_handlers_run_in_the_threadpool(service):
    from jobpilot.api.routes import accounts, job_board, knowledge, mail, profile, resumes, runs, settings, sources

    routes = {}
    for module in (accounts, job_board, knowledge, mail, profile, resumes, runs, settings, sources):
        for route in module.router(service).routes:
            for method in route.methods:
                routes[(method, route.path)] = route.endpoint
    for key in THREADED:
        assert key in routes, key
        assert not inspect.iscoroutinefunction(routes[key]), f"{key} runs its database work on the event loop"


@pytest.mark.integration
def test_job_board_apply_reads_the_board_off_the_event_loop(service, client, monkeypatch):
    """board_snapshot scans every fresh listing; it ran on the loop once per selected job."""
    from jobpilot.api.routes import job_board

    service.dispatch = True
    service.db.set_setting("control", {"paused": False, "auto_submit": True})
    with service.db.session() as s:
        for n in range(3):
            s.add(Job(id=f"j{n}", identity=f"j{n}", url=f"https://fixture.test/{n}", status="ready"))
    threads, loop = [], set()

    def board(db, **kwargs):
        threads.append(threading.get_ident())
        return {"jobs": [{"id": "j0"}, {"id": "j1"}, {"id": "j2"}]}

    async def queue(job_id, mode, **kwargs):
        loop.add(threading.get_ident())  # this coroutine runs on the event loop's thread
        return {"id": "run-" + job_id}

    monkeypatch.setattr(job_board, "board_snapshot", board)
    monkeypatch.setattr(service, "queue", queue)
    response = client.post("/api/job-board/apply", json={"job_ids": ["j0", "j1", "j2"]})
    assert response.status_code == 200, response.text
    assert [q["job_id"] for q in response.json()["queued"]] == ["j0", "j1", "j2"]
    assert len(threads) == 1  # once for the whole selection, not once per job
    assert loop and not loop & set(threads)
