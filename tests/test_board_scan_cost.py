"""Board scans read each posting's detail once (2026-10-29).

A Workday tenant took 25-48 s per scan: 7 role searches of 5 pages each, then one sequential detail request for every
candidate, on every scan, although those postings were stored already. Postings with a stored listing (or rejected for
this profile within three days) are not read again, the list's own dates narrow Greenhouse before any detail request,
and details run four at a time."""

import json
from datetime import UTC, datetime, timedelta

import pytest

from jobpilot import jobboard
from jobpilot.db import Job, JobListing
from jobpilot.schemas import Profile

PROFILE = Profile(target_roles=["Software Engineer"], skills=["Python", "SQL", "AWS"])
CFG = jobboard.BoardSettings(early_career_only=True, max_age_hours=720)
SOURCE = {"url": "https://tenant.wd5.myworkdayjobs.com/Careers", "kind": "workday", "name": "Tenant", "id": "src-1"}


def wd_row(n, title="Software Engineer I", age="Posted 3 Days Ago"):
    return {"title": f"{title} {n}" if n else title, "externalPath": f"/job/Austin-TX/SWE_R{n}", "postedOn": age}


def fake_fetch(monkeypatch, rows_for, detail=None):
    calls = []

    async def fetch(url, **kwargs):
        calls.append((kwargs.get("method", "GET"), url, (kwargs.get("payload") or {}).get("searchText")))
        if kwargs.get("method") == "POST":
            query, offset = kwargs["payload"]["searchText"], kwargs["payload"]["offset"]
            return json.dumps({"jobPostings": rows_for(query, offset)}), url
        return json.dumps(detail or {"jobPostingInfo": {"location": "Austin, TX", "country": "United States",
                                                         "jobDescription": "<p>0-2 years Python</p>",
                                                         "startDate": datetime.now(UTC).date().isoformat()}}), url

    monkeypatch.setattr(jobboard, "fetch", fetch)
    return calls


def test_the_plan_leads_with_level_terms_and_limits_role_pages():
    plan = jobboard.workday_plan(PROFILE, CFG)
    assert [q for q, _ in plan[:3]] == ["new grad", "entry level", "early career"]
    assert dict(plan)["new grad"] == jobboard.WORKDAY_LEVEL_PAGES
    assert dict(plan)["Software Engineer"] == jobboard.WORKDAY_ROLE_PAGES
    assert len([q for q, _ in plan if q.casefold() == "new grad"]) == 1
    # A best-fit-first profile on a board that does not insist on early career searches only its roles.
    other = Profile(target_roles=["Product Manager"], job_priority="best_fit_first")
    assert jobboard.workday_plan(other, jobboard.BoardSettings()) == [("Product Manager", jobboard.WORKDAY_ROLE_PAGES)]


@pytest.mark.parametrize("text,days", [("Posted Today", 0), ("Posted Yesterday", 1), ("Posted 1 Day Ago", 1),
                                        ("Posted 23 Days Ago", 23), ("Posted 30+ Days Ago", None), ("", None)])
def test_workday_ages(text, days):
    assert jobboard.workday_age_days(text) == days


async def test_postings_up_to_the_window_are_read_not_only_the_last_week(monkeypatch):
    rows = [wd_row(1, age="Posted 3 Days Ago"), wd_row(2, age="Posted 20 Days Ago"), wd_row(3, age="Posted 30+ Days Ago")]
    fake_fetch(monkeypatch, lambda q, offset: rows if q == "new grad" and offset == 0 else [])
    posts, seen, _, errors = await jobboard.read_feed(SOURCE, PROFILE, CFG, datetime.now(UTC))
    assert sorted(p.title for p in posts) == ["Software Engineer I 1", "Software Engineer I 2"] and not errors
    short = jobboard.BoardSettings(early_career_only=True, max_age_hours=168)
    posts, *_ = await jobboard.read_feed(SOURCE, PROFILE, short, datetime.now(UTC))
    assert [p.title for p in posts] == ["Software Engineer I 1"]


async def test_known_postings_cost_no_detail_request(monkeypatch):
    rows = [wd_row(n) for n in range(1, 6)]
    calls = fake_fetch(monkeypatch, lambda q, offset: rows if q == "new grad" and offset == 0 else [])
    known_links = {SOURCE["url"] + r["externalPath"] for r in rows[:4]}

    async def known(links):
        return known_links.intersection(links)

    posts, seen, _, _ = await jobboard.read_feed(SOURCE, PROFILE, CFG, datetime.now(UTC), known)
    assert [p.title for p in posts] == ["Software Engineer I 5"]
    assert len(seen) == 5  # still seen: they stay listed
    assert [c for c in calls if c[0] == "GET"] == [("GET", "https://tenant.wd5.myworkdayjobs.com/wday/cxs/tenant/Careers/job/Austin-TX/SWE_R5", None)]


async def test_paging_stops_when_a_page_adds_nothing(monkeypatch):
    full = [{"title": f"Accountant {n}", "externalPath": f"/job/x/ACC_R{n}", "postedOn": "Posted Today"} for n in range(20)]
    calls = fake_fetch(monkeypatch, lambda q, offset: full if offset == 0 else full)
    await jobboard.read_feed(SOURCE, PROFILE, CFG, datetime.now(UTC))
    posts = [c for c in calls if c[0] == "POST"]
    # One page per search: the first brought no candidate for this profile, so deeper pages were never asked for.
    assert len(posts) == len(jobboard.workday_plan(PROFILE, CFG))


async def test_a_tenant_that_refuses_every_search_is_an_error_not_an_empty_board(monkeypatch):
    class Refused(Exception):
        pass

    async def fetch(url, **kwargs):
        raise Refused("422")

    monkeypatch.setattr(jobboard, "fetch", fetch)
    with pytest.raises(Refused):
        await jobboard.read_feed(SOURCE, PROFILE, CFG, datetime.now(UTC))


async def test_one_failed_search_does_not_fail_the_board(monkeypatch):
    rows = [wd_row(1)]

    async def fetch(url, **kwargs):
        if kwargs.get("method") == "POST":
            if kwargs["payload"]["searchText"] == "junior":
                raise RuntimeError("429")
            return json.dumps({"jobPostings": rows if kwargs["payload"]["searchText"] == "new grad" else []}), url
        return json.dumps({"jobPostingInfo": {"location": "Austin, TX", "country": "US", "jobDescription": "x",
                                              "startDate": datetime.now(UTC).date().isoformat()}}), url

    monkeypatch.setattr(jobboard, "fetch", fetch)
    posts, _, complete, errors = await jobboard.read_feed(SOURCE, PROFILE, CFG, datetime.now(UTC))
    assert len(posts) == 1 and errors == 1 and not complete


async def test_greenhouse_reads_no_detail_for_a_posting_older_than_the_window(monkeypatch):
    now = datetime.now(UTC)
    jobs = [
        {"id": 1, "title": "Software Engineer, New Grad", "location": {"name": "Austin, TX"},
         "first_published": (now - timedelta(days=2)).isoformat(), "updated_at": now.isoformat()},
        {"id": 2, "title": "Software Engineer, New Grad", "location": {"name": "Austin, TX"},
         "first_published": (now - timedelta(days=200)).isoformat(), "updated_at": now.isoformat()},
        {"id": 3, "title": "Software Engineer, New Grad", "location": {"name": "Austin, TX"}, "updated_at": now.isoformat()},
    ]
    calls = []

    async def fetch(url, **kwargs):
        calls.append(url)
        if url.endswith("/jobs"):
            return json.dumps({"jobs": jobs}), url
        job = next(j for j in jobs if url.endswith(f"/jobs/{j['id']}"))
        return json.dumps({"first_published": job.get("first_published"), "company_name": "ExampleSoft", "content": "0-2 years"}), url

    monkeypatch.setattr(jobboard, "fetch", fetch)
    source = {"url": "https://job-boards.greenhouse.io/example", "kind": "greenhouse", "name": "Example", "id": "g"}
    posts, seen, *_ = await jobboard.read_feed(source, PROFILE, CFG, now)
    assert len(seen) == 3
    assert [u.rsplit("/", 1)[1] for u in calls if not u.endswith("/jobs")] == ["1", "3"]  # 2 is 200 days old


async def test_scan_stores_once_then_only_refreshes_and_remembers_rejections(service, monkeypatch):
    db = service.db
    db.set_setting("profile", PROFILE.model_dump())
    db.set_setting("job_board_settings", CFG.model_dump())
    good, bad = wd_row(1), wd_row(2)
    reads = []

    async def fetch(url, **kwargs):
        if kwargs.get("method") == "POST":
            rows = [good, bad] if kwargs["payload"]["searchText"] == "new grad" and kwargs["payload"]["offset"] == 0 else []
            return json.dumps({"jobPostings": rows}), url
        reads.append(url)
        senior = url.endswith("R2")  # a 9-years-of-experience posting is rejected by the screen
        text = "9 years of experience building systems" if senior else "0-2 years Python"
        return json.dumps({"jobPostingInfo": {"location": "Austin, TX", "country": "United States", "jobDescription": text,
                                              "startDate": datetime.now(UTC).date().isoformat()}}), url

    monkeypatch.setattr(jobboard, "fetch", fetch)
    first = await jobboard.scrape_source(SOURCE, db, service.config)
    assert len(first) == 1 and len(reads) == 2
    report = db.get_setting("job_board_scan:src-1")
    assert report["matched"] == 1 and report["excluded"] == 1 and report["state"] == "complete"

    # Backdate the listing's last_seen: the second scan must refresh it without reading either posting again.
    with db.exclusive() as s:
        listing = s.query(JobListing).one()
        listing.last_seen = (datetime.now(UTC) - timedelta(hours=9)).isoformat()
    second = await jobboard.scrape_source(SOURCE, db, service.config)
    assert second == [] and len(reads) == 2
    with db.session() as s:
        listing = s.query(JobListing).one()
        assert datetime.fromisoformat(listing.last_seen) > datetime.now(UTC) - timedelta(minutes=5) and listing.active
    assert db.get_setting("job_board_scan:src-1")["known"] == 1

    # A changed profile reads the rejected posting again.
    db.set_setting("profile", Profile(target_roles=["Software Engineer"], skills=["Python", "SQL", "AWS", "Go"]).model_dump())
    await jobboard.scrape_source(SOURCE, db, service.config)
    assert len(reads) == 3 and reads[-1].endswith("R2")
