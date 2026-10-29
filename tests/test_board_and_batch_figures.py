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
        # A job from before the batch started never counts toward it.
        s.add(Run(id="before", job_id="j9", state="confirmed", created_at=iso(90)))
        batch = {"state": "active", "target": 3, "started_at": iso(60), "run_ids": ["r0", "r1", "r2", "r3"]}
        s.add(Setting(key="job_board_autopilot", value=batch))
    figures = batch_snapshot(service)
    assert figures["confirmed"] == 1 == figures["confirmed_by_batch"] and figures["confirmed_by_owner"] == 0
    assert (figures["awaiting_answer"], figures["failed"], figures["queued"]) == (1, 1, 1)
    assert figures["attempted_jobs"] == 4 and figures["remaining"] == 2


def test_the_goal_counts_every_confirmed_job_since_the_batch_started_whoever_started_it(service):
    """The batch showed 220 while 300+ were confirmed: the owner's Apply, bulk Apply and apply-many runs are never in
    run_ids (2026-10-29)."""
    with service.db.exclusive() as s:
        for n in range(5):
            s.add(Job(id=f"c{n}", identity=f"goal-{n}", url=f"https://example.test/{n}", title="Engineer"))
        s.add(Job(id="demo", identity="goal-demo", url="https://example.test/d", title="Engineer", demo=True))
        s.add_all(
            [
                Run(id="own", job_id="c0", state="confirmed", created_at=iso(30)),
                # The owner's own Apply, and a retry of the same job: one job, counted once.
                Run(id="mine1", job_id="c1", state="confirmed", created_at=iso(20)),
                Run(id="mine1b", job_id="c1", state="confirmed", created_at=iso(10)),
                Run(id="mine2", job_id="c2", state="confirmed", created_at=iso(5)),
                # Evidence not settled yet, a failure, a demo job and a job confirmed before the batch: not counted.
                Run(id="pending", job_id="c3", state="confirmed", created_at=iso(5), receipt={"evidence_reconciliation": "pending"}),
                Run(id="failed", job_id="c4", state="failed", created_at=iso(5)),
                Run(id="demo-run", job_id="demo", state="confirmed", created_at=iso(5)),
                Run(id="old", job_id="c4", state="confirmed", created_at=iso(300)),
            ]
        )
        s.add(Setting(key="job_board_autopilot", value={"state": "active", "target": 4, "started_at": iso(60), "run_ids": ["own"]}))
    figures = batch_snapshot(service)
    assert figures["confirmed"] == 3 and figures["confirmed_by_batch"] == 1 and figures["confirmed_by_owner"] == 2
    assert figures["remaining"] == 1


def test_a_batch_without_a_start_time_counts_only_its_own_runs(service):
    with service.db.exclusive() as s:
        s.add_all([Job(id=f"d{n}", identity=f"nostart-{n}", url=f"https://example.test/{n}", title="Engineer") for n in range(2)])
        s.add_all([Run(id="a", job_id="d0", state="confirmed", created_at=iso(3)), Run(id="b", job_id="d1", state="confirmed", created_at=iso(3))])
        s.add(Setting(key="job_board_autopilot", value={"state": "active", "target": 3, "run_ids": ["a"]}))
    assert batch_snapshot(service)["confirmed"] == 1


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


def test_a_ready_job_the_batch_did_not_start_carries_its_reason_only_while_nothing_has_been_done_to_it(client, service):
    seed_board(service, n=10)
    reason = {"reason": "Employer already has 25 confirmed or reserved application(s); limit 25", "code": "company_limit", "outcome": "excluded", "at": iso(1)}
    # b0001 is not applied to; b0000 has a run; b0003 has a waiting owner Apply: both are in Pipeline, not Discovered.
    service.db.set_setting("owner_apply:b0003", {"at": iso(), "code": "worker_capacity", "reason": "All workers busy"})
    service.db.set_setting(
        "job_board_autopilot",
        {"state": "active", "target": 5, "run_ids": ["b-run"], "rejections": {"b0001": reason, "b0000": reason, "b0003": reason}},
    )
    board = client.get("/api/job-board", params={"include_sources": "false"}).json()
    carried = {j["id"]: j.get("rejection") for j in board["jobs"]}
    assert carried["b0001"] == reason and carried["b0002"] is None
    assert not carried.get("b0000") and not carried.get("b0003")
    # The batch bookkeeping stays out of the payloads.
    assert "rejections" not in board["autopilot"]
    assert "rejections" not in client.get("/api/snapshot", params={"include_sources": "false"}).json()["autopilot"]


def test_source_health_counts_boards_that_keep_failing_and_ignores_switched_off_ones(client, service):
    from jobpilot.db import Source
    from jobpilot.features.discovery.tables import DcSourceHealth

    ids = []
    for n in range(3):
        added = client.post("/api/job-board/sources", json={"name": f"Example {n}", "kind": "greenhouse", "url": f"https://boards.example.test/{n}"})
        ids.append(added.json()["id"])
    with service.db.exclusive() as s:
        s.add_all(
            [
                DcSourceHealth(source_id=ids[0], failures=34, last_error="HTTPStatusError"),
                DcSourceHealth(source_id=ids[1], failures=1, last_error="ConnectTimeout"),
                DcSourceHealth(source_id=ids[2], failures=9, last_error="HTTPStatusError"),
            ]
        )
        s.get(Source, ids[2]).enabled = False
    board = client.get("/api/job-board").json()
    assert board["source_health"] == {"failing": 2, "dead": 1}
    health = {x["id"]: x["health"] for x in board["sources"]}
    assert health[ids[0]]["failures"] == 34 and health[ids[0]]["last_error"] == "HTTPStatusError"
    ok = client.get("/api/job-board", params={"include_sources": "false"}).json()
    assert ok["sources"] == [] and ok["source_health"] == {"failing": 2, "dead": 1}


async def test_the_tick_measures_the_goal_the_same_way_as_the_dashboard(service, monkeypatch):
    """The tick counted only the batch's own runs (222) while the dashboard counted 436 (2026-10-29)."""
    from jobpilot import autopilot
    from jobpilot.jobboard import BoardSettings

    service.dispatch = lambda *args: None
    service.db.set_setting("control", {"auto_submit": True})
    monkeypatch.setattr(autopilot, "settings", lambda db: BoardSettings(auto_apply=True))
    with service.db.exclusive() as s:
        s.add_all([Job(id=f"t{n}", identity=f"tick-{n}", url=f"https://example.test/t{n}", title="Engineer") for n in range(3)])
        s.add_all([Run(id=f"tr{n}", job_id=f"t{n}", state="confirmed", created_at=iso(5)) for n in range(3)])
        s.add(Setting(key="job_board_autopilot", value={"state": "active", "target": 3, "started_at": iso(60), "run_ids": ["tr0"]}))
    await autopilot.tick(service)
    batch = service.db.get_setting("job_board_autopilot")
    assert batch["state"] == "complete" and batch["confirmed"] == 3


def test_a_board_the_owner_switched_off_stays_off_after_a_settings_save(service):
    from jobpilot.db import Source
    from jobpilot.jobboard import BoardSettings, owner_switched, setup_sources

    with service.db.exclusive() as s:
        s.add_all([Source(id=f"src{n}", name=f"Example {n}", kind="greenhouse", url=f"https://job-boards.greenhouse.io/example{n}",
                          cron="0 */2 * * *", enabled=True) for n in range(2)])
    service.db.set_setting("job_board_sources", {"ids": ["src0", "src1"]})
    owner_switched(service.db, "src0", False)
    with service.db.exclusive() as s:
        s.get(Source, "src0").enabled = False
    setup_sources(service.db, BoardSettings(enabled=True))
    with service.db.session() as s:
        assert s.get(Source, "src0").enabled is False and s.get(Source, "src1").enabled is True
    owner_switched(service.db, "src0", True)
    setup_sources(service.db, BoardSettings(enabled=True))
    with service.db.session() as s:
        assert s.get(Source, "src0").enabled is True
