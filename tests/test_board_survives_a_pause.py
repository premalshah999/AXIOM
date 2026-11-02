"""The Job board said "No open postings right now" after a Mac slept: every listing's `last_seen` was older than the board's
6-hour cutoff, though nothing had checked and found any of them gone. Production, 2026-11-02: scans ran in bursts (1,440 boards
finished in one hour after 8 hours with almost none), and 2,710 of 5,270 active listings were more than 6 hours past their last
read. A listing is gone when its own board or list was read after it was last seen and no longer showed it; time alone, with
nobody looking, says nothing (up to three days, when it says the app has not checked for too long to call anything open)."""

from datetime import UTC, datetime, timedelta

from jobpilot.db import Job, JobListing, Setting, Source
from jobpilot.features.discovery.feeds import SWEEP_KEY
from jobpilot.jobboard import board_snapshot

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
