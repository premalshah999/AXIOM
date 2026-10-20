"""A board or feed import without a listing never passed the board screen, so it is not a planner candidate.

2026-10-18: 36 replacement boards were added as plain Sources, so their first sweep imported 983 postings
unscreened (people ops, technicians, senior roles) with no listing. Listing-less jobs counted as the
applicant's own pasted links, so the planner's classification batches filled with jobs the autopilot could
never dispatch. Only an applicant-added link (origin "manual") is a candidate without a listing.
"""

from datetime import UTC, datetime

from jobpilot.db import Job
from jobpilot.features.discovery import policy_for
from jobpilot.features.discovery.readiness import _candidates


def add(db, jid, origin):
    with db.exclusive() as s:
        s.add(Job(id=jid, url=f"https://jobs.ashbyhq.com/acme/{jid}", title="Software Engineer", company="Acme",
                  location="Remote, US", description="Build things. " * 40, ats="ashby", status="new",
                  identity="ashby:acme:" + jid))
    if origin:
        db.set_setting("job_origin:" + jid, {"kind": origin})


def test_only_applicant_links_are_candidates_without_a_listing(service):
    db = service.db
    add(db, "pasted", "manual")
    add(db, "board", "source")
    add(db, "feed", "new_grad_feed")
    add(db, "unknown", None)
    jobs, stats = _candidates(db, policy_for(db), datetime.now(UTC))
    ids = {j["id"] for j in jobs}
    assert "pasted" in ids
    assert not ids & {"board", "feed", "unknown"}
    assert stats["stale_or_undated"] >= 3
