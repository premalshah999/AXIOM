"""The Job board said "No open postings right now" after a Mac slept: every listing's `last_seen` was older than the board's
6-hour cutoff, though nothing had checked and found any of them gone. Production, 2026-11-02: scans ran in bursts (1,440 boards
finished in one hour after 8 hours with almost none), and 2,710 of 5,270 active listings were more than 6 hours past their last
read. A listing is gone when its own board or list was read after it was last seen and no longer showed it; time alone, with
nobody looking, says nothing (up to three days, when it says the app has not checked for too long to call anything open)."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from jobpilot.db import Job, JobListing, Source
from jobpilot.features.discovery.feeds import SWEEP_KEY, sweep
from jobpilot.jobboard import board_snapshot, keep_last_read, one_of_each, request_scan, scrape_source

AT = datetime.now(UTC)
HOURS_AGO = lambda n: (AT - timedelta(hours=n)).isoformat()  # noqa: E731


def listing(s, name, seen_hours_ago, source_id, kind="greenhouse"):
    if not s.get(Source, source_id) and not source_id.startswith("feed:"):
        s.add(Source(id=source_id, name=name, kind=kind, url="https://example.test/" + name, cron="0 * * * *"))
    job = Job(identity=name, url=f"https://example.test/{name}/jobs/12345678", title="Software Engineer I", company="ExampleSoft",
              location="New York, NY", ats=kind, status="new")
    s.add(job)
    s.flush()
    s.add(JobListing(job_id=job.id, source_id=source_id, posted_at=HOURS_AGO(30), date_basis="x", location="New York, NY",
                     career_level="Early career", match_reason="x", last_seen=HOURS_AGO(seen_hours_ago)))


def shown(service):
    return {j["company"] + "/" + j["url"].split("/")[3] for j in board_snapshot(service.db, limit=None, jobs_only=True)["jobs"]}


def test_a_listing_nobody_has_re_read_stays_open_for_a_while(service):
    with service.db.exclusive() as s:
        listing(s, "asleep", 20, "s-asleep")
    assert shown(service) == {"ExampleSoft/asleep"}


def test_a_listing_its_board_was_read_without_is_not_open(service):
    with service.db.exclusive() as s:
        listing(s, "gone", 20, "s-gone")
    service.db.set_setting("job_board_scan:s-gone", {"state": "complete", "started_at": HOURS_AGO(2), "finished_at": HOURS_AGO(1)})
    assert shown(service) == set()


def test_the_scan_that_last_saw_a_listing_does_not_close_it(service):
    """A scan stamps what it sees with its own start, and finishes later: its finish is not a read that missed the listing."""
    with service.db.exclusive() as s:
        listing(s, "own", 20, "s-own")
    service.db.set_setting("job_board_scan:s-own", {"state": "complete", "started_at": HOURS_AGO(20), "finished_at": HOURS_AGO(19.9)})
    assert shown(service) == {"ExampleSoft/own"}


def test_a_scan_that_failed_proves_nothing(service):
    with service.db.exclusive() as s:
        listing(s, "failed", 20, "s-failed")
    service.db.set_setting("job_board_scan:s-failed", {"state": "error", "started_at": HOURS_AGO(2), "finished_at": HOURS_AGO(1)})
    assert shown(service) == {"ExampleSoft/failed"}


def test_another_boards_scan_does_not_close_this_one(service):
    with service.db.exclusive() as s:
        listing(s, "quiet", 20, "s-quiet")
        listing(s, "busy", 1, "s-busy")
    service.db.set_setting("job_board_scan:s-busy", {"state": "complete", "started_at": HOURS_AGO(1), "finished_at": HOURS_AGO(0.5)})
    assert shown(service) == {"ExampleSoft/quiet", "ExampleSoft/busy"}


def test_a_listing_unread_for_three_days_is_not_called_open(service):
    with service.db.exclusive() as s:
        listing(s, "ancient", 80, "s-ancient")
    assert shown(service) == set()


def test_a_list_posting_the_latest_sweep_dropped_is_not_open_but_a_failed_sweep_proves_nothing(service):
    with service.db.exclusive() as s:
        listing(s, "dropped", 20, "feed:new-grad")
    service.db.set_setting(SWEEP_KEY, {"state": "error", "started_at": HOURS_AGO(2), "finished_at": HOURS_AGO(1)})
    assert shown(service) == {"ExampleSoft/dropped"}
    service.db.set_setting(SWEEP_KEY, {"state": "running", "started_at": HOURS_AGO(0.2), "finished_at": HOURS_AGO(30)})
    assert shown(service) == {"ExampleSoft/dropped"}  # a sweep under way has not said what it dropped
    service.db.set_setting(SWEEP_KEY, {"state": "done", "started_at": HOURS_AGO(2), "finished_at": HOURS_AGO(1)})
    assert shown(service) == set()


def test_recently_seen_listings_are_open_whatever_the_scan_says(service):
    with service.db.exclusive() as s:
        listing(s, "fresh", 2, "s-fresh")
    service.db.set_setting("job_board_scan:s-fresh", {"state": "complete", "started_at": HOURS_AGO(1), "finished_at": HOURS_AGO(0.5)})
    assert shown(service) == {"ExampleSoft/fresh"}


def missed_by_a_finished_read(service, name):
    """A listing past the window whose own board was read to the end without it."""
    with service.db.exclusive() as s:
        listing(s, name, 20, "s-" + name)
    service.db.set_setting("job_board_scan:s-" + name, {"state": "complete", "started_at": HOURS_AGO(2), "finished_at": HOURS_AGO(1.9)})
    assert shown(service) == set()


async def _no_queue(_source_id):
    return True


def test_the_next_scan_in_line_does_not_bring_back_what_the_last_read_missed(service):
    """Every board's flag is rewritten to "queued" at each search: the proof must outlive the flag, or every gone listing came back for a whole scan round."""
    missed_by_a_finished_read(service, "inline")
    service.db.set_setting("job_board_sources", {"ids": ["s-inline"]})
    asyncio.run(request_scan(service.db, _no_queue))
    assert service.db.get_setting("job_board_scan:s-inline", {})["state"] == "queued"
    assert shown(service) == set()


def test_a_read_that_fails_after_a_finished_one_keeps_what_that_one_proved(service):
    missed_by_a_finished_read(service, "broke")
    service.db.set_setting("job_board_sources", {"ids": ["s-broke"]})

    async def refused(_source_id):
        raise RuntimeError("the queue is down")

    asyncio.run(request_scan(service.db, refused))
    assert service.db.get_setting("job_board_scan:s-broke", {})["state"] == "error"
    assert shown(service) == set()


def test_the_flag_a_finished_read_left_before_this_field_existed_counts(service):
    """An installed app's flags have no `read_at` yet: the next flag takes their start, so updating does not bring anything back."""
    assert keep_last_read({"state": "complete", "started_at": "T1"}, {"state": "queued"}) == {"state": "queued", "read_at": "T1"}
    assert keep_last_read({"state": "error", "started_at": "T1"}, {"state": "queued"}) == {"state": "queued"}
    assert keep_last_read({"state": "queued", "started_at": "T2", "read_at": "T1"}, {"state": "running"}) == {"state": "running", "read_at": "T1"}


def test_scrape_source_keeps_and_renews_the_last_read(service, config, monkeypatch):
    with service.db.exclusive() as s:
        s.add(Source(id="s-real", name="Real", kind="greenhouse", url="https://example.test/real", cron="0 * * * *"))
    source = {"id": "s-real", "name": "Real", "kind": "greenhouse", "url": "https://example.test/real"}
    service.db.set_setting("profile", {"target_roles": ["Software Engineer"]})
    service.db.set_setting("job_board_scan:s-real", {"state": "complete", "started_at": HOURS_AGO(9), "finished_at": HOURS_AGO(8.9)})

    async def fails(*_a, **_k):
        raise RuntimeError("the board is down")

    monkeypatch.setattr("jobpilot.jobboard.read_feed", fails)
    with pytest.raises(RuntimeError):
        asyncio.run(scrape_source(source, service.db, config))
    flag = service.db.get_setting("job_board_scan:s-real", {})
    assert flag["state"] == "error" and flag["read_at"] == HOURS_AGO(9)

    async def reads(*_a, **_k):
        return [], set(), True, 0

    monkeypatch.setattr("jobpilot.jobboard.read_feed", reads)
    asyncio.run(scrape_source(source, service.db, config))
    flag = service.db.get_setting("job_board_scan:s-real", {})
    assert flag["state"] == "complete" and flag["read_at"] == flag["started_at"] > HOURS_AGO(1)


def test_a_new_sweep_does_not_bring_back_what_the_last_one_dropped(service, monkeypatch):
    with service.db.exclusive() as s:
        listing(s, "dropped", 20, "feed:new-grad")
    service.db.set_setting(SWEEP_KEY, {"state": "done", "started_at": HOURS_AGO(2), "finished_at": HOURS_AGO(1.9)})
    assert shown(service) == set()
    during = []

    async def collecting(*_a, **_k):
        during.append(shown(service))  # a sweep under way
        raise RuntimeError("no network")

    monkeypatch.setattr("jobpilot.features.discovery.feeds.collect", collecting)
    monkeypatch.setattr("jobpilot.agent_settings.require", lambda *_a, **_k: None)
    assert asyncio.run(sweep(service.db))["state"] == "error"
    assert during == [set()]
    assert shown(service) == set()  # and it failed: what the last good read proved stands


def test_a_sweep_whose_list_could_not_be_read_proves_nothing(service, monkeypatch):
    """A list that failed to load names nothing, so its postings have not been read without."""
    with service.db.exclusive() as s:
        listing(s, "onlist", 20, "feed:new-grad")

    async def one_list_down(*_a, **_k):
        return [], {"kept": 0, "feeds": {"a": {"rows": 3}, "b": {"error": "ConnectError"}}, "boards": {"swept": 0}, "only_from_board_sweep": 0}

    monkeypatch.setattr("jobpilot.features.discovery.feeds.collect", one_list_down)
    monkeypatch.setattr("jobpilot.agent_settings.require", lambda *_a, **_k: None)
    monkeypatch.setattr("jobpilot.features.discovery.feeds.harvest_boards", _nothing)
    monkeypatch.setattr("jobpilot.features.discovery.companies.harvest_companies", _nothing)
    monkeypatch.setattr("jobpilot.features.discovery.postings.fill_missing_text", _nothing)
    assert asyncio.run(sweep(service.db))["state"] == "done"
    assert shown(service) == {"ExampleSoft/onlist"}
    assert service.db.get_setting(SWEEP_KEY, {})["read_at"] == ""
    # and the next sweep, while it runs, still has no read to go on
    assert keep_last_read(service.db.get_setting(SWEEP_KEY, {}), {"state": "running"}) == {"state": "running", "read_at": ""}


async def _nothing(*_a, **_k):
    return {}


def test_the_batch_counts_read_listings_the_way_the_board_does(service):
    """The status line's Discovered and unattempted counts kept their own copy of the old 6-hour cutoff."""
    from jobpilot.autopilot import batch_snapshot

    with service.db.exclusive() as s:
        listing(s, "asleep", 20, "s-asleep")
    assert shown(service) == {"ExampleSoft/asleep"}
    assert batch_snapshot(service)["inventory"]["fresh"] == 1


def test_a_search_reads_one_board_of_each_platform_in_turn(service):
    """A new user's first minutes were all one platform, and all one host's traffic: the catalog lists every Ashby board first."""
    assert one_of_each([("a1", "ashby"), ("a2", "ashby"), ("a3", "ashby"), ("g1", "greenhouse"), ("w1", "workday"), ("g2", "greenhouse")]) == ["a1", "g1", "w1", "a2", "g2", "a3"]
    asked = []

    async def noted(source_id):
        asked.append(source_id)
        return True

    asyncio.run(request_scan(service.db, noted))
    with service.db.session() as s:
        kinds = {row.id: row.kind for row in s.execute(select(Source.id, Source.kind))}
    assert len(asked) > 100  # the catalog
    first = [kinds[source_id] for source_id in asked[:4]]
    assert len(set(first)) >= 3, first
