"""Workday loop: a submitted requisition on My Applications confirms; a blank SPA shell is measurable."""

import asyncio as real_asyncio
from types import SimpleNamespace

import pytest

from jobpilot import workday
from jobpilot.workday import WorkdayAuth

HOME = "https://caci.wd1.myworkdayjobs.com"
JOB_URL = f"{HOME}/external/job/Lorton-VA-US/Software-Safety-Developer-Analyst_332049"
DASHBOARD = f"{HOME}/en-US/external/jobTasks/completed/application"

SUBMITTED_DASHBOARD = """<!doctype html><html><body>
<div role="dialog" aria-modal="true" data-automation-id="popUpDialog"><h2>Application Submitted</h2>
<p>We have received your application for employment.</p></div>
<h2>My Applications</h2>
<table data-automation-id="applicationsTable">
<tr><th>Job Title</th><th>Job Req</th><th>My Application Status</th><th>Date Submitted</th></tr>
<tr data-automation-id="applicationRow"><td>Software Safety Developer/Analyst</td><td>332049</td><td>Application Received</td><td>October 12, 2026</td></tr>
<tr data-automation-id="applicationRow"><td>AI Engineer</td><td>333066</td><td>Application Received</td><td>October 12, 2026</td></tr>
</table></body></html>"""

OTHER_REQ_ONLY = "\n".join(
    line for line in SUBMITTED_DASHBOARD.splitlines() if "Software Safety Developer/Analyst" not in line
)

BLANK_SHELL = "<!doctype html><html><head><title>Workday</title></head><body></body></html>"


class FakeEngine:
    def __init__(self, page, url=JOB_URL):
        self.page = page
        self.job = {"id": "job-1", "ats": "workday", "url": url}
        self.result = None
        self.events = []
        self.run_id = "run-1"
        self.armed = False
        self.form = SimpleNamespace(
            resolver=SimpleNamespace(
                job=self.job,
                run_id="run-1",
                profile=SimpleNamespace(
                    email="alex@example.test", allow_account_creation=True, accept_all_application_terms=False
                ),
            ),
            proof=self._no_proof,
            scan=self._scan,
            evidence_context=lambda: {"job_id": "job-1", "run_id": "run-1"},
        )
        self.db = SimpleNamespace(
            get_setting=lambda key, default=None: default if default is not None else {}
        )
        self.email_verification = SimpleNamespace(state=lambda observation: ("none", None))

    async def _no_proof(self):
        return None

    async def _scan(self):
        return {}

    def emit(self, kind, message, data=None):
        self.events.append({"kind": kind, "message": message, "data": data or {}})


@pytest.fixture
def fast_loop(monkeypatch):
    """Skip the loop's 0.5 s and 0.7 s waits; the 60-iteration budget and its branches stay real."""

    async def no_sleep(_seconds=0):
        await real_asyncio.sleep(0)

    monkeypatch.setattr(workday, "asyncio", SimpleNamespace(sleep=no_sleep))


async def serve(page, url, body):
    async def route(request):
        await request.fulfill(status=200, content_type="text/html", body=body)

    await page.context.route(f"{HOME}/**", route)
    await page.goto(url, wait_until="domcontentloaded")


async def test_submitted_requisition_on_dashboard_confirms_without_resubmitting(page, fast_loop):
    await serve(page, DASHBOARD, SUBMITTED_DASHBOARD)
    engine = FakeEngine(page)

    assert await WorkdayAuth(engine).run() is True

    assert engine.result["state"] == "confirmed"
    receipt = engine.result["receipt"]
    assert receipt["reconciled_existing_submission"] is True
    assert receipt["evidence_selector"] == "workday_my_applications_requisition_row"
    assert "332049" in receipt["confirmation"] and "Application Received" in receipt["confirmation"]
    assert not any(e["kind"] in {"account", "unexplained_state"} for e in engine.events)


async def test_other_requisition_on_dashboard_never_confirms_this_job(page, fast_loop):
    await serve(page, DASHBOARD, OTHER_REQ_ONLY)
    engine = FakeEngine(page)

    with pytest.raises(ValueError, match="did not reach an application form"):
        await WorkdayAuth(engine).run()

    assert (engine.result or {}).get("state") != "confirmed"


async def test_blank_spa_shell_emits_unexplained_state_during_the_blank_phase(page, fast_loop, monkeypatch):
    await serve(page, JOB_URL, BLANK_SHELL)
    engine = FakeEngine(page)

    async def no_wait(*_args, **_kwargs):
        return None

    monkeypatch.setattr(page, "wait_for_timeout", no_wait)

    with pytest.raises(ValueError, match="did not reach an application form"):
        await WorkdayAuth(engine).run()

    shapes = [e["data"]["iteration"] for e in engine.events if e["kind"] == "unexplained_state"]
    # Iterations 6 and 12 of a blank stretch (the page_blank reload and reset points), plus the
    # exhaustion emit at the end. Without the fix only the exhaustion emit (iteration 0) exists.
    assert 6 in shapes and 12 in shapes
    assert any(e["kind"] == "page_blank" for e in engine.events)
