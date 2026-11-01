"""The Jobs "Confirmed" chip, the Applications ledger and Overview's total are one number (2026-11-01).

The installed app showed Done 3,302 on Jobs beside 3,156 confirmed on Applications. Done also held 146 jobs where the employer
said "you already applied" and one dry run: nothing AXIOM sent. One definition now (api/pipeline.py confirmed_jobs), used by
the ledger, the chip and the snapshot. Synthetic data only.
"""

import pytest
from sqlalchemy import insert

from jobpilot.api import pipeline
from jobpilot.db import Job, Run

from test_pipeline_counts import at, job, run

pytestmark = pytest.mark.integration


def seed(service):
    jobs = [job(n) for n in range(1, 9)]
    jobs[6]["demo"] = True  # a demo job is never counted anywhere
    runs = [
        run(1, "job00001", "confirmed", created=at(90), finished=at(80)),
        # Confirmed, then a follow-up attempt failed: still one confirmed application.
        run(2, "job00002", "confirmed", created=at(90), finished=at(80)),
        run(3, "job00002", "waiting_browser", created=at(40), finished=at(30)),
        # The employer already had an application: nothing was sent, so it is not CONFIRMED.
        run(4, "job00003", "already_applied", created=at(70), finished=at(60)),
        # A dry run sends nothing either.
        run(5, "job00004", "dry_run_passed", created=at(70), finished=at(60)),
        # Evidence still being reconciled: not counted as confirmed anywhere.
        {**run(6, "job00005", "confirmed", created=at(70), finished=at(60)), "receipt": {"evidence_reconciliation": "pending"}},
        run(7, "job00007", "confirmed", created=at(70), finished=at(60)),  # the demo job
        run(8, "job00006", "failed", created=at(70), finished=at(60)),
    ]
    with service.db.exclusive() as s:
        s.execute(insert(Job), jobs)
        s.execute(insert(Run), runs)


def test_the_chip_the_ledger_and_the_snapshot_count_the_same_confirmed_jobs(client, service):
    seed(service)
    counts = client.get("/api/snapshot?include_sources=false").json()
    ledger = client.get("/api/successful-applications").json()["applications"]
    assert sorted(a["job_id"] for a in ledger) == ["job00001", "job00002"]
    assert counts["counts"]["confirmed"] == counts["stats"]["confirmed_total"] == counts["counts"]["groups"]["done"] == len(ledger) == 2
    # Clicking the chip shows that many rows, and they are the ledger's jobs.
    page = client.get("/api/jobs?group=done&size=50").json()
    assert page["total"] == len(page["rows"]) == 2
    assert sorted(r["job"]["id"] for r in page["rows"]) == sorted(a["job_id"] for a in ledger)


def test_already_applied_and_dry_runs_are_not_confirmed(client, service):
    seed(service)
    groups = client.get("/api/snapshot?include_sources=false").json()["counts"]["groups"]
    assert groups["existing"] == 1  # the employer's own "already applied", its own chip
    assert groups["stopped"] == 1  # the dry run
    assert groups["unconfirmed"] == 1  # confirmed with evidence still pending reads as unconfirmed, not done
    existing = client.get("/api/jobs?group=existing").json()
    assert [r["job"]["id"] for r in existing["rows"]] == ["job00003"] and existing["total"] == 1


def test_the_group_counts_add_up_to_the_pipeline(service):
    seed(service)
    with service.db.session() as s:
        counts = pipeline.counts(s)
    inside = sum(n for g, n in counts["groups"].items() if g not in {*pipeline.OUTSIDE, "all"})
    assert counts["pipeline"] == inside == counts["groups"]["all"]


def test_a_confirmed_job_whose_later_attempt_failed_is_flagged_confirmed_in_its_row(client, service):
    """Listed under the Confirmed chip, the row must not read "Not submitted" because a follow-up attempt failed."""
    seed(service)
    rows = {r["job"]["id"]: r for r in client.get("/api/jobs?group=done&size=50").json()["rows"]}
    assert rows["job00002"]["run"]["state"] == "waiting_browser" and rows["job00002"]["confirmed"] is True
    stopped = client.get("/api/jobs?group=stopped&size=50").json()["rows"]
    assert [r["confirmed"] for r in stopped] == [False]
