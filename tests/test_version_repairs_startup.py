"""Startup's changed-strategy repairs read run metadata, then only the candidate drafts' packets: loading every saved
packet (6,498 runs) OOM-killed the server every 40 seconds on 2026-10-24."""

from sqlalchemy import insert

from jobpilot.db import Job, Run, Setting
from jobpilot.recovery import schedule_version_repairs


def seed(service):
    jobs = [
        dict(id=f"job{n}", identity=f"example.test/{n}", url=f"https://boards.example.test/{n}", title="Engineer",
             company=f"Example {n}", location="Remote", ats="greenhouse", status="waiting_browser", demo=False,
             created_at="2026-10-23T10:00:00+00:00")
        for n in range(4)
    ]
    draft = {"match_approved": True, "profile": {"autonomous": True}, "engine_version": "execution-v2-old",
             "blocker_type": "control_failure"}

    def run(n, job, state, created, packet=None):
        return dict(id=f"run{n}", job_id=job, state=state, mode="submit", packet=packet or draft, receipt={},
                    reason="", created_at=created, finished_at=created)

    runs = [
        run(0, "job0", "waiting_browser", "2026-10-23T11:00:00+00:00"),  # a stalled draft from an older engine
        run(1, "job1", "confirmed", "2026-10-23T10:00:00+00:00"),  # submitted earlier: protected
        run(2, "job1", "waiting_browser", "2026-10-23T11:00:00+00:00"),
        run(3, "job2", "waiting_browser", "2026-10-23T10:00:00+00:00"),  # superseded by a newer failed run
        run(4, "job2", "failed", "2026-10-23T11:00:00+00:00"),
        run(5, "job3", "waiting_browser", "2026-10-23T11:00:00+00:00", {**draft, "match_approved": False}),
    ]
    with service.db.exclusive() as s:
        s.execute(insert(Job), jobs)
        s.execute(insert(Run), runs)


def test_only_the_latest_unprotected_stalled_draft_gets_a_repair(service):
    seed(service)
    assert schedule_version_repairs(service) == ["job0"]
    with service.db.session() as s:
        assert s.get(Setting, "draft_recovery:job0").value["source_run_id"] == "run0"
    # A release does not reset the same budget again.
    assert schedule_version_repairs(service) == []
