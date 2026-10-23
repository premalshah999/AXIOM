"""Exact dashboard counts and the paged Pipeline list (2026-10-22).

The snapshot sent the newest 1,000 jobs and 1,000 runs and the dashboard counted those: with 7,000+ jobs every count
was wrong, and an old job with a fresh run was missing altogether. Counts are now SQL aggregates over every job's
latest run; lists page through GET /api/jobs. Everything here is synthetic.
"""

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import insert

from jobpilot.api import pipeline
from jobpilot.db import Job, Review, Run, Setting

pytestmark = pytest.mark.integration

MODEL_TS = Path(__file__).resolve().parents[1] / "frontend" / "src" / "model.ts"


def at(minutes_ago):
    return (datetime.now(UTC) - timedelta(minutes=minutes_ago)).isoformat()


def job(n, *, status="ready", created=None, company="Acme Example"):
    return dict(
        id=f"job{n:05d}",
        identity=f"example.test/jobs/{n}",
        url=f"https://boards.example.test/acme/jobs/{n}",
        title=f"Platform Engineer {n}",
        company=company,
        location="Remote, United States",
        ats="greenhouse",
        status=status,
        demo=False,
        created_at=created or at(10_000 - n),
    )


def run(n, job_id, state, *, created, finished=None):
    return dict(
        id=f"run{n:05d}",
        job_id=job_id,
        state=state,
        mode="submit",
        packet={"worker_pool": "normal"},
        receipt={"website_confirmed": True} if state == "confirmed" else {},
        created_at=created,
        finished_at=finished,
    )


def seed_history(service):
    """1,200 jobs and 1,400 runs: more of each than the old snapshot sent."""
    jobs, runs = [], []
    for n in range(1, 1201):
        jobs.append(job(n))
    # 1,100 jobs have a failed attempt; 200 of them were retried and then also stopped (cancelled).
    for n in range(1, 1101):
        runs.append(run(n, f"job{n:05d}", "failed", created=at(9_000 - n), finished=at(9_000 - n)))
    for n in range(1, 201):
        runs.append(run(2000 + n, f"job{n:05d}", "cancelled", created=at(500 - n), finished=at(500 - n)))
    # The oldest job's newest run is applying right now: it was beyond the newest 1,000 jobs.
    runs.append(run(5000, "job00001", "queued", created=at(1)))
    # Two confirmed today, one waiting for an answer with a question, one unconfirmed.
    runs.append(run(5001, "job01101", "confirmed", created=at(30), finished=at(20)))
    runs.append(run(5002, "job01102", "confirmed", created=at(30), finished=at(20)))
    runs.append(run(5003, "job01103", "waiting_answer", created=at(5)))
    runs.append(run(5004, "job01104", "submission_unknown", created=at(40), finished=at(39)))
    with service.db.exclusive() as s:
        s.execute(insert(Job), jobs)
        s.execute(insert(Run), runs)
        s.add(Review(run_id="run05003", question="What is your notice period?", key="custom:notice"))
        # The owner paused a job that was queued, and one that is already confirmed (its submission stands).
        s.add(Run(id="run05005", job_id="job01105", state="queued", mode="submit", created_at=at(2)))
        s.add(Setting(key="job_control:job01105", value={"state": "paused"}))
        s.add(Setting(key="job_control:job01101", value={"state": "paused"}))


def test_counts_are_exact_with_more_jobs_and_runs_than_the_old_snapshot_sent(client, service):
    seed_history(service)
    counts = client.get("/api/snapshot?include_sources=false").json()["counts"]
    # job00001: queued; 2..200: cancelled (their retry); 201..1100: failed; 1101..1105 as seeded; 1106..1200 not started.
    assert counts["groups"] == {
        "active": 1,
        "needs": 1,
        "repair": 900,
        "unconfirmed": 1,
        "paused": 1,  # the queued job the owner paused
        "done": 2,  # a pause never hides a confirmed submission
        "stopped": 199,
        "new": 95,
        "all": 1105,
    }
    assert counts["pipeline"] == 1105 and counts["jobs"] == 1200
    assert counts["states"]["queued"] == 1  # the paused one reads as paused, not queued
    assert counts["states"]["failed"] == 900 and counts["states"]["cancelled"] == 199


def test_today_and_total_confirmations_count_every_run(client, service):
    seed_history(service)
    stats = client.get("/api/snapshot?include_sources=false").json()["stats"]
    assert stats["confirmed_today"] == 2 and stats["confirmed_total"] == 2


def test_the_snapshot_carries_live_rows_and_recent_outcomes_not_history(client, service):
    seed_history(service)
    snap = client.get("/api/snapshot?include_sources=false").json()
    jobs = {j["id"] for j in snap["jobs"]}
    # The old job applying now, the question, the paused queued job and recent outcomes; never 1,000 rows.
    assert {"job00001", "job01103", "job01105", "job01101"} <= jobs
    assert len(snap["runs"]) <= 60 and all("packet" not in r for r in snap["runs"])
    assert [q["run_id"] for q in snap["reviews"]] == ["run05003"]


def test_pipeline_pages_list_every_row_of_a_group_attention_first(client, service):
    seed_history(service)
    first = client.get("/api/jobs", params={"size": 5}).json()
    assert first["total"] == 1105 and first["pages"] == 221
    # In progress, then needs you, then repairing (model.ts PRIORITY).
    assert [row["job"]["id"] for row in first["rows"][:2]] == ["job00001", "job01103"]
    assert first["rows"][1]["reviews"][0]["question"] == "What is your notice period?"
    assert first["rows"][0]["attempts"] == 3 and "description" not in first["rows"][0]["job"]
    stopped = client.get("/api/jobs", params={"group": "stopped", "size": 100, "page": 2}).json()
    assert stopped["total"] == 199 and len(stopped["rows"]) == 99
    assert {row["run"]["state"] for row in stopped["rows"]} == {"cancelled"}
    paused = client.get("/api/jobs", params={"group": "paused"}).json()["rows"]
    assert [(row["job"]["id"], row["control"]["state"]) for row in paused] == [("job01105", "paused")]


def test_pipeline_search_and_a_page_past_the_end(client, service):
    seed_history(service)
    found = client.get("/api/jobs", params={"q": "engineer 1103"}).json()
    assert [row["job"]["id"] for row in found["rows"]] == ["job01103"]
    late = client.get("/api/jobs", params={"group": "needs", "page": 40}).json()
    assert late["page"] == 1 and late["total"] == 1 and len(late["rows"]) == 1
    assert client.get("/api/jobs", params={"group": "everything"}).status_code == 422


def test_the_job_panel_lists_every_attempt_without_packets(client, service):
    seed_history(service)
    runs = client.get("/api/runs", params={"job_id": "job00001"}).json()["runs"]
    assert [r["state"] for r in runs] == ["queued", "cancelled", "failed"]
    assert all("packet" not in r for r in runs)


def test_a_tie_on_creation_time_still_gives_one_latest_run(service):
    with service.db.exclusive() as s:
        s.add(Job(**job(1)))
        s.add_all([Run(id="a", job_id="job00001", state="failed", created_at=at(1)),
                   Run(id="b", job_id="job00001", state="queued", created_at=at(1))])
    with service.db.session() as s:
        counts = pipeline.counts(s)
    assert counts["pipeline"] == 1 and counts["states"] == {"queued": 1}


def ts_groups():
    """state -> group, as model.ts STATES declares it."""
    text = MODEL_TS.read_text()
    block = text[text.index("export const STATES") : text.index("export const GROUPS")]
    return dict(re.findall(r'^  "?([\w:]+)"?: \{.*?group: "(\w+)"', block, re.S | re.M))


def test_the_server_groups_states_as_the_dashboard_does():
    # "pending:" states are the dashboard's own optimistic marks, never stored.
    expected = {s: g for s, g in ts_groups().items() if not s.startswith("pending:")}
    run_states = {s: g for s, g in expected.items() if not s.startswith("job:") and s not in {"pausing", "resumed"}}
    server = {state: group for group, states in pipeline.RUN_GROUPS.items() for state in states}
    # A run in "paused" reads as "resumed" (paused group); "pausing" is an owner pause over a running run.
    assert {**server, "paused": "paused"} == run_states
    job_states = {s.removeprefix("job:"): g for s, g in expected.items() if s.startswith("job:")}
    assert {status: group for group, statuses in pipeline.JOB_GROUPS.items() for status in statuses} == job_states
    committed = re.search(r"COMMITTED = new Set\(\[(.*?)\]\)", MODEL_TS.read_text(), re.S).group(1)
    assert set(re.findall(r'"(\w+)"', committed)) == set(pipeline.COMMITTED)
    priority = re.search(r"PRIORITY: Record<Group, number> = \{(.*?)\}", MODEL_TS.read_text(), re.S).group(1)
    assert dict((g, int(n)) for g, n in re.findall(r"(\w+): (\d+)", priority)) == pipeline.PRIORITY


def test_a_waiting_apply_reads_as_waiting_to_start_unless_its_job_already_started(client, service):
    seed_history(service)
    with service.db.exclusive() as s:
        for job_id in ("job01150", "job00500", "job00001", "job01105"):
            s.add(Setting(key="owner_apply:" + job_id, value={"reason": "All workers are busy"}))
    counts = client.get("/api/snapshot?include_sources=false").json()["counts"]
    # job01150 (never run) and job00500 (failed) wait to start; job00001 is already queued; job01105 is paused.
    assert counts["states"]["pending:waiting"] == 2 and counts["states"]["queued"] == 1
    assert counts["groups"]["active"] == 3 and counts["groups"]["paused"] == 1
    assert counts["groups"]["repair"] == 899 and counts["pipeline"] == 1106
