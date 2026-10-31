"""Database work the dashboard's writes put on the event loop (2026-10-29).

One asyncio loop drives the API, the queues and every browser owner. A handler that is `async def` runs its database
calls on that loop; a write also waits for the per-app advisory lock, which another thread can hold for seconds.
"""

import inspect



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

