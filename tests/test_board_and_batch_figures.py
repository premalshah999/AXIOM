"""Jobs › Discovered totals and paging, the Batch page's figures and the readiness block (2026-10-22).

The board sent the newest 500 listings and the dashboard filtered and counted them, so a board with more fresh
listings showed wrong totals and missed rows; the Batch figures read every run of the installation on each poll; the
readiness block answered 500 whenever a fresh listing had no classification gate. Synthetic data only.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import insert

from jobpilot.autopilot import batch_snapshot
from jobpilot.db import Job, JobListing, Run, Setting

pytestmark = pytest.mark.integration


def iso(minutes_ago=0):
    return (datetime.now(UTC) - timedelta(minutes=minutes_ago)).isoformat()


def seed_board(service, n=600):
    """`n` fresh US listings: every third below fit, every fifth unranked, the rest eligible; the first is queued."""
    jobs, listings = [], []
    for i in range(n):
        status, score = ("skipped", 30) if i % 3 == 0 else ("new", None) if i % 5 == 0 else ("ready", 85)
        jobs.append(
            dict(
                id=f"b{i:04d}",
                identity=f"board-{i}",
                url=f"https://boards.greenhouse.io/acme{i}/jobs/{i}",
                title=f"Software Engineer {i}",
                company=f"Acme Example {i}",
                location="Remote, United States",
                ats="greenhouse" if i % 2 else "ashby",
                status="queued" if i == 0 else status,
                score=score,
                demo=False,
                created_at=iso(i),
            )
        )
        listings.append(
            dict(
                job_id=f"b{i:04d}",
                source_id="src",
                posted_at=iso(60 + i),
                date_basis="posted",
                location="Remote, United States",
                country="US",
                career_level="New grad",
                match_reason="Title matches",
                active=True,
                first_seen=iso(60 + i),
                last_seen=iso(5),
            )
        )
    with service.db.exclusive() as s:
        s.execute(insert(Job), jobs)
        s.execute(insert(JobListing), listings)
        s.add(Run(id="b-run", job_id="b0000", state="queued", mode="submit", packet={}))
    service.db.set_setting("job_board_settings", {"enabled": True, "minimum_auto_fit": 70, "max_age_hours": 720})


def test_discovered_counts_every_fresh_job_not_applied_to_and_pages_them(client, service):
    seed_board(service)
    board = client.get("/api/job-board", params={"include_sources": "false", "page": 1, "size": 25}).json()
    # b0000 has a run: it is in Pipeline, not Discovered (owner, 2026-10-23).
    assert board["fresh_total"] == 599 and board["total"] == 599 and len(board["jobs"]) == 25
    assert board["fits"] == {"all": 599, "eligible": 320, "uncertain": 0, "unranked": 80, "below": 199}
    assert board["platforms"] == ["ashby", "greenhouse"]
    below = client.get(
        "/api/job-board", params={"include_sources": "false", "page": 3, "size": 50, "fit": "below"}
    ).json()
    assert below["total"] == 199 and below["page"] == 3 and len(below["jobs"]) == 50
    assert {j["status"] for j in below["jobs"]} == {"skipped"}
    # "Select all" covers every matching job, not only the page.
    assert below["selectable_total"] == 199 and "b0000" not in below["selectable_ids"]
    assert "description" not in below["jobs"][0]
    found = client.get(
        "/api/job-board", params={"include_sources": "false", "page": 1, "q": "acme example 599", "ats": "ashby"}
    ).json()
    assert found["total"] == 0  # 599 is on greenhouse
    legacy = client.get("/api/job-board", params={"include_sources": "false"}).json()
    assert len(legacy["jobs"]) == 500 and "total" not in legacy


def test_batch_figures_follow_each_batch_jobs_newest_run(service):
    with service.db.exclusive() as s:
        for n in range(4):
            s.add(Job(id=f"j{n}", identity=f"batch-{n}", url=f"https://example.test/{n}", title="Engineer"))
        s.add_all(
            [
                # j0 failed in the batch, and its recovery (not listed in run_ids) was confirmed: it counts once.
                Run(id="r0", job_id="j0", state="waiting_browser", created_at=iso(30)),
                Run(id="r0b", job_id="j0", state="confirmed", created_at=iso(10), receipt={"website_confirmed": True}),
                Run(id="r1", job_id="j1", state="waiting_answer", created_at=iso(5)),
                Run(id="r2", job_id="j2", state="failed", created_at=iso(5)),
                Run(id="r3", job_id="j3", state="queued", created_at=iso(1)),
            ]
        )
        s.add(Job(id="j9", identity="outside", url="https://example.test/9", title="Engineer"))
        # A job outside the batch never counts toward it.
        s.add(Run(id="other", job_id="j9", state="confirmed", created_at=iso(2)))
        batch = {"state": "active", "target": 3, "run_ids": ["r0", "r1", "r2", "r3"]}
        s.add(Setting(key="job_board_autopilot", value=batch))
    figures = batch_snapshot(service)
    assert figures["confirmed"] == 1
    assert (figures["awaiting_answer"], figures["failed"], figures["queued"]) == (1, 1, 1)
    assert figures["attempted_jobs"] == 4 and figures["remaining"] == 2


def test_the_readiness_block_answers_when_a_fresh_listing_has_no_gate(client, service):
    seed_board(service, n=3)
    answer = client.get("/api/v2/discovery/status")
    assert answer.status_code == 200, answer.text
    assert answer.json()["fresh_jobs"] >= 1


def test_a_waiting_apply_moves_a_job_from_discovered_to_the_pipeline(client, service):
    seed_board(service, n=10)
    service.db.set_setting("owner_apply:b0001", {"at": iso(), "code": "worker_capacity", "reason": "All workers busy"})
    board = client.get("/api/job-board", params={"include_sources": "false", "page": 1}).json()
    assert "b0001" not in {j["id"] for j in board["jobs"]} and board["fresh_total"] == 8
    snap = client.get("/api/snapshot?include_sources=false").json()
    # The tab, the Batch inventory and the page agree.
    assert snap["counts"]["discovered"] == 8 == snap["autopilot_runtime"]["inventory"]["discovered"]
    assert snap["autopilot_runtime"]["inventory"]["fits"] == {
        fit: n for fit, n in board["fits"].items() if fit != "all"
    }
    active = client.get("/api/jobs", params={"group": "active"}).json()["rows"]
    waiting = {row["job"]["id"]: row for row in active}
    assert waiting["b0001"]["request"]["reason"] == "All workers busy" and waiting["b0001"]["run"] is None
    assert snap["counts"]["states"]["pending:waiting"] == 1


def test_a_raised_target_applies_to_the_running_batch_and_a_lower_one_never_shrinks_it(service):
    """Owner 2026-10-29: 150 more applications past a running batch of 200."""
    from jobpilot.autopilot import start_batch

    db = service.db
    started = start_batch(db, 200)
    assert start_batch(db, 350)["target"] == 350
    assert start_batch(db, 100)["target"] == 350  # A default or older target never cuts the running goal.
    assert db.get_setting("job_board_autopilot")["started_at"] == started["started_at"]  # Never restarted.
