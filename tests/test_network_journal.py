"""Value-free network journal (REA analysis follow-up, 2026-10-17).

REA showed failures hide in network behaviour that the DOM never shows (search requests that load options, an Oracle
400 whose reason appears only in a transient toast). Each step record now carries what the site did since the previous
step: method, type, path (long tokens redacted), query keys (never values), status, timing, and the server's own error
text for a failed XHR/fetch with emails and long numbers redacted.
"""

import json

from jobpilot.forms import NetworkMonitor


async def test_the_journal_records_requests_and_redacted_server_errors(page):
    async def handler(route):
        url = route.request.url
        if url.endswith("/apply"):
            await route.fulfill(status=200, content_type="text/html", body="<p>form</p>")
        elif url.endswith("/api/save"):
            await route.fulfill(
                status=400,
                content_type="application/json",
                body=json.dumps({"errors": [{"message": "Education: The Degree field is required for alex@example.com"}]}),
            )
        else:
            await route.fulfill(status=200, content_type="application/json", body="{}")

    await page.route("https://ats.example.test/**", handler)
    await page.goto("https://ats.example.test/apply")
    monitor = NetworkMonitor(page)
    await page.evaluate("""async () => {
      await fetch('/api/options?query=Data%20Science&tenant=x');
      await fetch('/api/save', {method: 'POST', body: '{}'});
      await fetch('/api/verify/AbCdEf0123456789AbCdEf0123456789');
    }""")
    await page.wait_for_timeout(300)
    entries = monitor.take_journal()
    by_path = {e["path"]: e for e in entries}
    assert set(by_path) == {"/api/options", "/api/save", "/api/verify/[token]"}
    assert by_path["/api/options"]["status"] == 200 and by_path["/api/options"]["query"] == ["query", "tenant"]
    failed = by_path["/api/save"]
    assert failed["status"] == 400 and failed["method"] == "POST" and failed["type"] == "fetch"
    assert "The Degree field is required" in failed["message"] and "example.com" not in failed["message"]
    assert monitor.take_journal() == []


def test_repair_evidence_shows_only_the_failed_requests(service, tmp_path):
    from jobpilot import repair_agent
    from jobpilot.db import Job, Run
    from jobpilot.schemas import Profile

    with service.db.exclusive() as s:
        job = Job(identity="nj-1", url="https://ats.example.test/apply", company="Acme", title="Engineer", ats="oracle")
        s.add(job)
        s.flush()
        run = Run(job_id=job.id, state="waiting_browser", mode="submit", packet={}, reason="did not advance")
        s.add(run)
        s.flush()
        run_id = run.id
    steps = service.config.data_dir / "runs" / run_id / "steps"
    steps.mkdir(parents=True)
    (steps / "01-form.json").write_text(json.dumps({
        "url": "https://ats.example.test/apply", "headings": [], "errors": [], "fields": [], "controls": [],
        "network": [
            {"method": "GET", "path": "/api/options", "status": 200},
            {"method": "POST", "path": "/api/save", "status": 400, "message": "Education: The Degree field is required"},
        ],
    }))
    evidence = repair_agent.evidence(service.db, service.config.data_dir, run_id, Profile())
    assert evidence["page"]["network_failures"] == [
        {"method": "POST", "path": "/api/save", "status": 400, "message": "Education: The Degree field is required"}
    ]


async def test_workday_option_sources_are_kept_for_the_step_record(page):
    """REA/journal finding: Workday renders application questions from questionnaire definitions and pickers from
    values lists. Keep those payloads (employer content) so pickers can be answered from data, not by opening each."""
    definition = {"questions": [{"id": "q1", "text": "Will you require sponsorship?", "answers": ["Yes", "No"]}]}

    async def handler(route):
        url = route.request.url
        if url.endswith("/apply"):
            await route.fulfill(status=200, content_type="text/html", body="<p>form</p>")
        elif "/questionnaire/" in url:
            await route.fulfill(status=200, content_type="application/json", body=json.dumps(definition))
        else:
            await route.fulfill(status=200, content_type="application/json", body='{"data":[{"id":"1","descriptor":"Mobile"}]}')

    await page.route("https://acme.wd5.myworkdayjobs.com/**", handler)
    await page.goto("https://acme.wd5.myworkdayjobs.com/apply")
    monitor = NetworkMonitor(page)
    await page.evaluate("""async () => {
      await fetch('/wday/calypso/cxs/jobapplication/acme/questionnaire/AbCdEf0123456789AbCdEf0123456789/definition?type=primary', {method: 'POST'});
      await fetch('/wday/calypso/cxs/jobapplication/acme/values/phone/deviceTypes');
      await fetch('/wday/calypso/cxs/jobapplication/acme/jobapplication/AbCdEf0123456789AbCdEf0123456789/name', {method: 'POST'});
    }""")
    await page.wait_for_timeout(300)
    catalog = monitor.take_catalog()
    assert [c["path"].rsplit("/", 2)[-2:] for c in catalog] == [["[token]", "definition"], ["phone", "deviceTypes"]]
    assert catalog[0]["payload"] == definition and catalog[0]["query"] == ["type"]
    assert monitor.take_catalog() == []


async def test_grouped_workday_values_are_not_a_flat_catalog(page):
    class Response:
        headers = {}
        url = "https://example.test/values/fieldsOfStudy"

        def __init__(self, data):
            self.data = data

        async def body(self):
            return json.dumps(self.data).encode()

    monitor = NetworkMonitor(page)
    await monitor._catalog(Response([{"descriptor": "Data Science", "id": "1"}]))
    assert monitor.value_list("fieldsOfStudy") == ["Data Science"]
    await monitor._catalog(Response([
        {"descriptor": "Partial List (First 500 Entries)", "href": "/groups/partial"},
        {"descriptor": "All", "href": "/groups/all"},
    ]))
    assert monitor.value_list("fieldsOfStudy") is None
