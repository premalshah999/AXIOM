"""An employer's "you already applied" page is not a submission AXIOM made.

Pair: the same page counts when AXIOM's own earlier attempt on the job started a commit (a "submitting" event: the first
run's answer was lost and the second found the proof), and does not when nothing of AXIOM's ever committed on that job."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import insert

from jobpilot.api.success import success
from jobpilot.db import Event, Job, Run

pytestmark = pytest.mark.integration
NOW = datetime(2026, 10, 31, 12, tzinfo=UTC)


def at(days_ago):
    return (NOW - timedelta(days=days_ago)).isoformat()


def seed(service, name, runs, events=()):
    """runs: [(id, state, receipt, days_ago)]; events: [(run id, kind)]."""
    with service.db.session() as s:
        s.execute(insert(Job), [dict(id=name, identity=f"example.test/{name}", url=f"https://example.test/{name}",
                                     title="Engineer", company="Example Co", location="Remote", ats="workday",
                                     status="ready", demo=False, created_at=at(30))])
        s.execute(insert(Run), [dict(id=i, job_id=name, state=st, mode="submit", reason="", packet={}, receipt=rc,
                                     created_at=at(d)) for i, st, rc, d in runs])
        s.execute(insert(Event), [dict(run_id=r, kind=k, message="", data={}, created_at=at(1)) for r, k in events]) if events else None


NOTICE = {"existing_application_notice": True, "reconciled_existing_submission": True}


def tally(service):
    with service.db.session() as s:
        return success(s, 7, now=NOW)["overall"]


def test_existing_notice_without_an_owned_attempt_is_already_applied(service):
    seed(service, "theirs", [("t1", "confirmed", NOTICE, 1)])
    t = tally(service)
    assert t["confirmed"] == 0 and t["already_applied"] == 1 and t["first_try_pct"] in (None, 0.0)


def test_existing_notice_after_our_own_commit_still_counts(service):
    seed(service, "ours", [("o1", "submission_unknown", {}, 3), ("o2", "confirmed", NOTICE, 1)], events=[("o1", "submitting")])
    t = tally(service)
    assert t["confirmed"] == 1 and t["already_applied"] == 0


def test_a_commit_made_after_the_notice_does_not_own_it(service):
    seed(service, "later", [("l1", "confirmed", NOTICE, 3), ("l2", "confirmed", {}, 1)], events=[("l2", "submitting")])
    t = tally(service)
    # the later run's own confirmation counts the job once; the earlier notice alone never did
    assert t["confirmed"] == 1 and t["final_pct"] == 100.0


def test_an_ordinary_confirmation_is_unaffected(service):
    seed(service, "plain", [("p1", "confirmed", {}, 1)])
    t = tally(service)
    assert t["confirmed"] == 1 and t["already_applied"] == 0
