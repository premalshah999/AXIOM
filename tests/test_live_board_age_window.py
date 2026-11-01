"""A posting on a public board API is open when listed, so it keeps a longer window than a source that cannot show that.

In a 30-day replay over 11,486 postings of 270 Greenhouse, Ashby and Lever boards, 55% were refused as older than 30 days
and 22 of 24 sampled pages of the 31-90 day ones still answered live; 60 days admits 89% more matches (2026-11-01).
Custom sites, short-link feeds and Workday's undated "30+ days" keep the user's own window."""

from datetime import UTC, datetime, timedelta

import pytest

from jobpilot.db import Job, JobListing, Source
from jobpilot.features.discovery.policy import DiscoveryPolicy, fresh
from jobpilot.features.discovery.router import policy_for
from jobpilot.jobboard import BoardSettings, Posting, board_snapshot, persist_scan, screen_fingerprint
from jobpilot.schemas import Profile

AT = datetime.now(UTC)
DAYS_AGO = lambda n: (AT - timedelta(days=n)).isoformat()  # noqa: E731


@pytest.mark.parametrize(
    "kwargs,kind,hours",
    [
        ({}, "greenhouse", 1440), ({}, "lever", 1440), ({}, "ashby", 1440), ({}, "smartrecruiters", 1440),
        ({}, "workday", 720), ({}, "career_page", 720), ({}, None, 720),
        ({"max_age_hours": 168}, "greenhouse", 168),  # a tighter choice is respected
        ({"live_board_age_hours": 2160}, "greenhouse", 2160), ({"live_board_age_hours": 48}, "lever", 48),
        ({"live_board_age_hours": 2160}, "workday", 720),
    ],
)
def test_the_window_depends_on_whether_the_source_can_show_a_posting_is_live(kwargs, kind, hours):
    assert BoardSettings(**kwargs).window_hours(kind) == hours


def test_the_scan_keeps_a_45_day_old_board_posting_and_drops_it_from_a_custom_site(service):
    profile = Profile(target_roles=["Software Engineer"], skills=["Python"])
    cfg = BoardSettings(early_career_only=True)
    kept = {}
    for kind in ("greenhouse", "career_page"):
        post = Posting(f"https://example.test/{kind}/jobs/12345678", "Software Engineer I", "ExampleSoft", "New York, NY",
                       "Build things.", "US", DAYS_AGO(45), "Employer first_published")
        report = {"matched": 0, "old": 0, "unknown_date": 0, "excluded": 0}
        created, _ = persist_scan(service.db, {"id": "s-" + kind, "kind": kind}, [post], {post.url}, False, cfg, profile, AT, report)
        kept[kind] = (len(created), report["old"])
    assert kept == {"greenhouse": (1, 0), "career_page": (0, 1)}


def test_the_planner_and_the_board_read_the_same_window(service):
    service.db.set_setting("job_board_settings", {"early_career_only": True})
    policy = policy_for(service.db)
    assert (policy.max_age_days, policy.live_max_age_days) == (30, 60)
    old = DAYS_AGO(45)
    assert fresh(old, "verified", None, policy, AT, "greenhouse")[0]
    assert not fresh(old, "verified", None, policy, AT, "career_page")[0]
    assert not fresh(old, "verified", None, policy, AT)[0]
    assert not fresh(DAYS_AGO(70), "verified", None, DiscoveryPolicy(), AT, "greenhouse")[0]


def test_the_board_snapshot_lists_the_old_board_posting_only(service):
    with service.db.exclusive() as s:
        for kind in ("greenhouse", "career_page"):
            s.add(Source(id="s-" + kind, name=kind, kind=kind, url="https://example.test/" + kind, cron="0 * * * *"))
            job = Job(identity=kind, url=f"https://example.test/{kind}/jobs/12345678", title="Software Engineer I",
                      company="ExampleSoft", location="New York, NY", ats=kind, status="new")
            s.add(job)
            s.flush()
            s.add(JobListing(job_id=job.id, source_id="s-" + kind, posted_at=DAYS_AGO(45), date_basis="x",
                             location="New York, NY", career_level="Early career", match_reason="x", last_seen=AT.isoformat()))
    titles = {j["ats"] for j in board_snapshot(service.db, limit=None, jobs_only=True)["jobs"]}
    assert titles == {"greenhouse"}


def test_a_changed_window_reads_rejected_postings_again():
    profile = Profile(target_roles=["Software Engineer"])
    assert screen_fingerprint(profile, BoardSettings()) != screen_fingerprint(profile, BoardSettings(live_board_age_hours=720))
