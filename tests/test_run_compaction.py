"""Finished runs give up the profile copy and, when nothing can resume them, the checkpoint. Nothing is lost."""

import json

import pytest
from sqlalchemy import func, select

from jobpilot import run_profile
from jobpilot.cli import compact_runs as compact_command
from jobpilot.db import Job, ProfileSnapshot, Run
from jobpilot.pipeline_comparison import CONTINUABLE_STATES
from jobpilot.run_profile import NEVER_RESUMED, compact_runs, full, slimmed
from jobpilot.runtime import profile_version

PROFILE = {"name": "Alex Example", "email": "alex+ats@example.test", "autonomous": True, "location": "Boston, MA",
           "facts": {"first_name": "Alex"}, "reviewed_answers": {"q" * 40: "a" * 2000}}


@pytest.fixture(autouse=True)
def forget_remembered():
    run_profile._REMEMBERED.clear()


def add_run(db, state="confirmed", packet=None, job="job-1", run_id=None):
    with db.session() as s:
        if not s.get(Job, job):
            s.add(Job(id=job, identity=job, url="https://example.test/" + job))
        row = Run(job_id=job, state=state, packet=packet if packet is not None else {
            "profile": dict(PROFILE), "profile_version": profile_version(PROFILE), "resume_sha": "abc",
            "browser_checkpoint": {"ledger": {"x": "y" * 500}, "resume_sha": "abc", "failed_strategies": {"a": 1}},
        })
        if run_id:
            row.id = run_id
        s.add(row)
        s.flush()
        return row.id


def test_only_runs_nothing_can_resume_lose_their_checkpoint():
    packet = {"profile": PROFILE, "browser_checkpoint": {"ledger": {}}}
    for state in NEVER_RESUMED:
        assert "browser_checkpoint" not in slimmed(state, packet)[0]
    for state in {*CONTINUABLE_STATES, "failed", "submission_unknown"}:
        new, profile = slimmed(state, packet)
        assert "browser_checkpoint" in new, state
        assert profile == PROFILE and "reviewed_answers" not in new["profile"], "the profile copy goes in every state"
    assert not NEVER_RESUMED & CONTINUABLE_STATES
    assert not NEVER_RESUMED & {"waiting_browser", "timed_out", "failed", "submission_unknown"}, (
        "recovery._eligible_specialist_history and retries read these checkpoints"
    )


def test_compaction_is_a_dry_run_by_default_and_reports_bytes(service):
    db = service.db
    run_id = add_run(db)
    before = json.dumps(db.list(Run)[0]["packet"], sort_keys=True)
    report = compact_runs(db, pause=0)
    assert report["applied"] is False and report["runs_changed"] == 1 and report["profiles_removed"] == 1
    assert report["checkpoints_removed"] == 1 and report["snapshots_added"] == 1
    assert report["raw_bytes_after"] < report["raw_bytes_before"] and report["stored_bytes_saved_estimate"] > 0
    with db.session() as s:
        assert json.dumps(s.get(Run, run_id).packet, sort_keys=True) == before, "nothing changed"
        assert s.scalar(select(func.count()).select_from(ProfileSnapshot)) == 0


def test_compaction_applies_losslessly_and_then_finds_nothing_to_do(service):
    db = service.db
    finished = add_run(db, "confirmed")
    waiting = add_run(db, "waiting_browser", job="job-2")
    report = compact_runs(db, apply=True, pause=0)
    assert report["applied"] and report["runs_changed"] == 2 and report["snapshots_added"] == 1
    assert report["checkpoints_removed"] == 1
    with db.session() as s:
        done, parked = s.get(Run, finished).packet, s.get(Run, waiting).packet
    assert full(done, db) == PROFILE and full(parked, db) == PROFILE, "each still rebuilds the exact profile"
    assert done["profile"]["email"] == PROFILE["email"] and done["profile_version"] == profile_version(PROFILE)
    assert "browser_checkpoint" not in done and parked["browser_checkpoint"]["failed_strategies"] == {"a": 1}
    assert done["resume_sha"] == "abc", "everything else stays"
    again = compact_runs(db, apply=True, pause=0)
    assert again["runs_changed"] == 0 and again["snapshots_added"] == 0


@pytest.mark.parametrize("state", sorted(run_profile.ACTIVE))
def test_compaction_never_touches_a_run_in_flight(service, state):
    db = service.db
    run_id = add_run(db, state)
    with db.session() as s:
        before = s.get(Run, run_id).packet
    report = compact_runs(db, apply=True, pause=0)
    assert report["skipped_active"] == 1 and report["runs_changed"] == 0
    with db.session() as s:
        assert s.get(Run, run_id).packet == before


def test_a_scheduled_recovery_keeps_its_jobs_checkpoints(service):
    db = service.db
    run_id = add_run(db, "cancelled")
    db.set_setting("draft_recovery:job-1", {"scheduled": True, "source_run_id": run_id})
    report = compact_runs(db, apply=True, pause=0)
    assert report["skipped_recovery"] == 1 and report["profiles_removed"] == 1
    with db.session() as s:
        assert s.get(Run, run_id).packet["browser_checkpoint"]


def test_a_continuation_copying_a_slim_packet_still_finds_the_profile(service):
    db = service.db
    source = add_run(db, "waiting_browser")
    compact_runs(db, apply=True, pause=0)
    with db.session() as s:
        copied = {**s.get(Run, source).packet, "source_run_id": source}
    assert full(copied, db) == PROFILE and copied["browser_checkpoint"]["resume_sha"] == "abc"


def test_the_command_reports_and_changes_nothing_unless_told(service, config):
    run_id = add_run(service.db)
    said = []
    assert compact_command(config, False, say=lambda *a, **k: said.append(a[0])) == 0
    assert "Nothing changed" in said[-1] and json.loads(said[0])["runs_changed"] == 1
    with service.db.session() as s:
        assert "reviewed_answers" in s.get(Run, run_id).packet["profile"]
