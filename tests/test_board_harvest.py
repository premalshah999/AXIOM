"""Boards named by the feeds' full histories become managed sources automatically (2026-10-29).

The orchestrator added the live boards of SimplifyJobs' listing histories (internship repos included) by hand; 230 of
3,800 referenced boards were still unmanaged. The feed sweep now does it daily: bounded, deduplicated against every
stored source, each proven live by the board's own public API first."""

from datetime import UTC, datetime, timedelta

import httpx

from jobpilot.db import Source
from jobpilot.features.discovery import feeds
from jobpilot.jobboard import managed_ids

ROWS = [
    {"company_name": "ExampleSoft", "url": "https://job-boards.greenhouse.io/examplesoft/jobs/1", "active": False},
    {"company_name": "Acme Labs", "url": "https://jobs.lever.co/acmelabs/abc-123", "active": False},
    {"company_name": "Ghost Inc", "url": "https://jobs.ashbyhq.com/ghost/uuid", "active": True},
    {"company_name": "Known Co", "url": "https://boards.greenhouse.io/knownco/jobs/9", "active": True},
    {"company_name": "Wide Co", "url": "https://widecorp.wd5.myworkdayjobs.com/en-US/WideCareers/job/x/y_R1", "active": False},
    {"company_name": "Emptyville", "url": "https://jobs.smartrecruiters.com/Emptyville/744", "active": True},
    {"company_name": "Shortlink", "url": "https://go.vansh.nyc/i2", "active": True},
]
LIVE = {
    "https://boards-api.greenhouse.io/v1/boards/examplesoft/jobs": {"jobs": [{"id": 1}]},
    "https://api.lever.co/v0/postings/acmelabs": [{"id": "x"}],
    "https://boards-api.greenhouse.io/v1/boards/knownco/jobs": {"jobs": [{"id": 1}]},
    "https://api.smartrecruiters.com/v1/companies/Emptyville/postings": {"totalFound": 0, "content": []},
    "https://widecorp.wd5.myworkdayjobs.com/wday/cxs/widecorp/WideCareers/jobs": {"total": 12, "jobPostings": []},
}  # ghost's Ashby board is a 404


def client():
    def handler(request):
        url = str(request.url).split("?")[0]
        if url == "https://history.example.test/listings.json":
            return httpx.Response(200, json=ROWS)
        if url in LIVE:
            return httpx.Response(200, json=LIVE[url])
        return httpx.Response(404)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_boards_reference_every_ats_in_a_history_row():
    boards = feeds.boards_from([feeds.Posting(r["company_name"], "", r["url"], [], "", "verified", "h") for r in ROWS])
    assert {k for k, _, _ in boards} == {"greenhouse", "lever", "ashby", "workday", "smartrecruiters"}


def test_source_urls_are_the_forms_the_scanner_reads():
    assert feeds.board_source_url("greenhouse", "x") == "https://job-boards.greenhouse.io/x"
    assert feeds.board_source_url("workday", "widecorp.wd5.myworkdayjobs.com|widecorp|WideCareers") == \
        "https://widecorp.wd5.myworkdayjobs.com/WideCareers"


async def test_live_unmanaged_boards_are_added_once_and_dead_ones_remembered(service):
    db = service.db
    db.set_setting("job_board_settings", {"enabled": True, "interval_hours": 2})
    with db.exclusive() as s:
        known = Source(name="Known Co", kind="greenhouse", url="https://job-boards.greenhouse.io/KnownCo", cron="0 */2 * * *")
        s.add(known)
        s.flush()
        known_id = known.id
    db.set_setting("job_board_sources", {"ids": [known_id]})
    history = (("test", "https://history.example.test/listings.json"),)
    async with client() as http:
        report = await feeds.harvest_boards(db, client=http, feeds=history)
    assert report["added"] == 3  # examplesoft, acmelabs, widecorp; knownco exists, ghost is 404, emptyville has no postings
    with db.session() as s:
        urls = {r.kind: r.url for r in s.query(Source).all() if r.name != "Known Co"}
    assert urls == {"greenhouse": "https://job-boards.greenhouse.io/examplesoft", "lever": "https://jobs.lever.co/acmelabs",
                    "workday": "https://widecorp.wd5.myworkdayjobs.com/WideCareers"}
    assert len(managed_ids(db)) == 4
    state = db.get_setting(feeds.HARVEST_KEY)
    assert set(state["checked"]) == {"ashby|ghost", "smartrecruiters|emptyville"}

    # Within a day nothing runs; later, boards that failed are not asked again for two weeks and nothing is added twice.
    async with client() as http:
        assert (await feeds.harvest_boards(db, client=http, feeds=history))["state"] == "skipped"
        later = datetime.now(UTC) + timedelta(days=2)
        again = await feeds.harvest_boards(db, client=http, feeds=history, at=later)
    assert again["added"] == 0 and again["validated"] == 0


async def test_additions_per_harvest_are_bounded_and_resume_next_time(service):
    db = service.db
    db.set_setting("job_board_settings", {"enabled": True, "interval_hours": 2})
    async with client() as http:
        report = await feeds.harvest_boards(db, client=http, feeds=(("test", "https://history.example.test/listings.json"),), limit=1)
    assert report["live"] == 4 and report["added"] == 1


async def test_a_disabled_board_or_an_excluded_ats_adds_nothing(service):
    db = service.db
    db.set_setting("job_board_settings", {"enabled": False})
    async with client() as http:
        assert (await feeds.harvest_boards(db, client=http, feeds=(("test", "https://history.example.test/listings.json"),)))["state"] == "disabled"
        db.set_setting("job_board_settings", {"enabled": True})
        db.set_setting("ats_focus", {"exclude": ["greenhouse", "lever", "workday"]})
        report = await feeds.harvest_boards(db, client=http, feeds=(("test", "https://history.example.test/listings.json"),))
    assert report["added"] == 0
