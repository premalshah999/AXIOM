"""Bounded recovery covers hard ATSs, excluding dead, protected and owner-waiting jobs."""

from datetime import UTC, datetime

from jobpilot.autopilot import persist_stalled
from jobpilot.db import Job, Review, Run

ROLES = ["Software Development Engineer", "Data Science"]


def seed(service, company, title, ats, state, reason, n):
    with service.db.exclusive() as s:
        job = Job(identity=f"ps-{n}", url=f"https://example.test/{n}", company=company, title=title, ats=ats, status="ready")
        s.add(job)
        s.flush()
        run = Run(job_id=job.id, state=state, mode="submit", packet={}, reason=reason,
                  created_at=datetime(2026, 10, 17, 1, n % 59, tzinfo=UTC).isoformat())
        s.add(run)
        s.flush()
        return job.id, run.id


async def test_free_slots_resume_the_right_stalled_jobs(service, monkeypatch):
    resumed = []

    async def fake_manage(service, job_id, action, **kwargs):
        resumed.append(job_id)
        return {"run_id": "r"}

    monkeypatch.setattr("jobpilot.job_control.manage", fake_manage)
    monkeypatch.setattr("jobpilot.autopilot.datetime", type("D", (), {"now": staticmethod(lambda tz=None: datetime(2026, 10, 17, 5, tzinfo=UTC))}))
    ok1, _ = seed(service, "Acme", "Software Engineer I", "workday", "waiting_browser", "did not advance", 1)
    same_employer, _ = seed(service, "Acme", "Data Scientist", "workday", "timed_out", "tab failed", 2)
    captcha, _ = seed(service, "Beta", "Software Engineer", "workday", "waiting_browser", "2Captcha: SOLVER_ERROR", 3)
    icims, _ = seed(service, "Gamma", "Software Engineer", "icims", "waiting_browser", "did not advance", 4)
    off_role, _ = seed(service, "Delta", "Data Analyst", "workday", "waiting_browser", "did not advance", 5)
    asking, asking_run = seed(service, "Eps", "Software Engineer", "successfactors", "needs_review", "needs answers", 6)
    with service.db.exclusive() as s:
        s.add(Review(run_id=asking_run, question="Q", options=[], key="k", reason=""))
    ef1, _ = seed(service, "Zeta", "Software Engineer", "eightfold", "waiting_browser", "did not advance", 7)
    ef2, _ = seed(service, "Eta", "Data Scientist", "eightfold", "waiting_browser", "did not advance", 8)
    ok2, _ = seed(service, "Theta", "SDE I", "successfactors", "waiting_browser", "did not advance", 9)
    batch = {}
    started = await persist_stalled(service, 10, ROLES, batch)
    assert set(resumed) == {ok1, icims, ef1, ok2} and started == 4 and len(batch["persisted"]) == 4
    assert same_employer not in resumed and captcha not in resumed
    assert off_role not in resumed and asking not in resumed and ef2 not in resumed


async def test_a_job_that_keeps_stalling_backs_off(service, monkeypatch):
    """Short repair backoff cannot bypass maintenance or the total retry limit."""
    from jobpilot.autopilot import PERSIST_DAILY, persist_wait

    resumed = []

    async def fake_manage(service, job_id, action, **kwargs):
        resumed.append(job_id)
        return {"run_id": "r"}

    monkeypatch.setattr("jobpilot.job_control.manage", fake_manage)
    monkeypatch.setattr("jobpilot.autopilot.datetime", type("D", (), {"now": staticmethod(lambda tz=None: datetime(2026, 10, 17, 5, 30, tzinfo=UTC))}))

    def job(company, reason, finished_minute, runs=1):
        with service.db.exclusive() as s:
            row = Job(identity=f"bo-{company}", url=f"https://example.test/{company}", company=company,
                      title="Software Engineer", ats="oracle", status="ready")
            s.add(row)
            s.flush()
            for i in range(runs):
                at = datetime(2026, 10, 17, 5, finished_minute - runs + 1 + i, tzinfo=UTC).isoformat()
                s.add(Run(job_id=row.id, state="waiting_browser", mode="submit", packet={}, reason=reason,
                          created_at=at, finished_at=at))
            return row.id

    fresh = job("Fresh", "did not advance", 30)  # just finished: waits 30 seconds
    rested = job("Rested", "did not advance", 26)  # one run, ended 4 minutes ago
    looping = job("Looping", "did not advance", 25, runs=4)  # 4 runs today: waits 24 minutes
    maintenance = job("Honeywell", "Oracle Cloud Fusion is showing a scheduled maintenance page", 10)
    spent = job("Spent", "did not advance", 15, runs=PERSIST_DAILY)
    await persist_stalled(service, 10, ROLES, {})
    assert resumed == [rested]
    assert persist_wait(1, "x").seconds == 30 and persist_wait(2, "x").seconds == 60
    assert persist_wait(1, "scheduled maintenance").seconds == 3600
    assert not {fresh, looping, maintenance, spent} & set(resumed)


async def test_runs_that_sent_nothing_do_not_use_the_days_retries(service, monkeypatch):
    # Goldman Sachs (2026-10-18): two deployment interruptions filled the daily quota of a job whose fix had deployed.
    from jobpilot.autopilot import PERSIST_DAILY

    resumed = []

    async def fake_manage(service, job_id, action, **kwargs):
        resumed.append(job_id)
        return {"run_id": "r"}

    monkeypatch.setattr("jobpilot.job_control.manage", fake_manage)
    monkeypatch.setattr("jobpilot.autopilot.datetime", type("D", (), {"now": staticmethod(lambda tz=None: datetime(2026, 10, 17, 5, 30, tzinfo=UTC))}))
    reasons = ["Worker interrupted before submission; draft recovery scheduled",
               "Another application kept the same email sender busy; nothing was sent. Requeue this job"] * PERSIST_DAILY
    with service.db.exclusive() as s:
        row = Job(identity="gs", url="https://example.test/gs", company="Goldman Sachs", title="Software Engineer",
                  ats="oracle", status="ready")
        s.add(row)
        s.flush()
        for i, reason in enumerate([*reasons, "The browser did not advance within its recovery budget"]):
            at = datetime(2026, 10, 17, 5, 10 + i, tzinfo=UTC).isoformat()
            s.add(Run(job_id=row.id, state="waiting_browser" if i % 2 == 0 or i == len(reasons) else "timed_out",
                      mode="submit", packet={}, reason=reason, created_at=at, finished_at=at))
        job_id = row.id
    await persist_stalled(service, 10, ROLES, {})
    assert resumed == [job_id]


async def test_a_paused_job_is_never_resumed_automatically(service, monkeypatch):
    """Emory, 2026-10-22: the stalled-run retry resumed a job 40 s after it was paused and spent another run on the
    failure the pause was for. Only the owner lifts a pause."""
    from jobpilot.db import Setting
    from jobpilot.job_control import manage

    monkeypatch.setattr("jobpilot.autopilot.datetime", type("D", (), {"now": staticmethod(lambda tz=None: datetime(2026, 10, 17, 5, tzinfo=UTC))}))
    paused, _ = seed(service, "Emory", "Data Engineer", "icims", "waiting_browser", "did not advance", 11)
    with service.db.exclusive() as s:
        s.add(Setting(key="job_control:" + paused, value={"state": "paused", "at": "2026-10-17T04:00:00+00:00"}))
    resumed = []

    async def fake_manage(service, job_id, action, **kwargs):
        resumed.append(job_id)

    monkeypatch.setattr("jobpilot.job_control.manage", fake_manage)
    assert await persist_stalled(service, 10, ["Data Engineer"], {}) == 0 and resumed == []
    monkeypatch.undo()
    import pytest

    with pytest.raises(ValueError, match="only the owner"):
        await manage(service, paused, "resume", actor="autopilot")
    assert service.db.get_setting("job_control:" + paused)["state"] == "paused"


async def test_retries_stop_after_three_runs_on_the_same_blocker_until_something_changes(service, monkeypatch):
    """A job re-run 14 times on one stalled form (2026-10-29): runs 4+ took 63% of the worker minutes for 7-15% confirmed."""
    resumed = []

    async def fake_manage(service, job_id, action, **kwargs):
        resumed.append(job_id)
        return {"run_id": "r"}

    monkeypatch.setattr("jobpilot.job_control.manage", fake_manage)
    monkeypatch.setattr("jobpilot.autopilot.datetime", type("D", (), {"now": staticmethod(lambda tz=None: datetime(2026, 10, 19, 9, tzinfo=UTC))}))
    from jobpilot.autopilot import SAME_STALL_RUNS, stall_key

    assert stall_key("Step 3 of 7 did not advance after 42s") == stall_key("Step 5 of 7 did not advance after 9s")

    def stalled(n, engines, reasons):
        with service.db.exclusive() as s:
            job = Job(identity=f"loop-{n}", url=f"https://example.test/loop{n}", company=f"Loop{n}",
                      title="Software Engineer", ats="workday", status="ready")
            s.add(job)
            s.flush()
            for i, (engine, reason) in enumerate(zip(engines, reasons)):
                s.add(Run(job_id=job.id, state="waiting_browser", mode="submit", packet={"engine_version": engine},
                          reason=reason, created_at=datetime(2026, 10, 17, 1, i, tzinfo=UTC).isoformat()))
            return job.id

    n = SAME_STALL_RUNS
    looping = stalled(1, ["v1"] * n, [f"Step {i} did not advance"for i in range(n)])
    new_build = stalled(2, ["v1"] * (n - 1) + ["v2"], ["did not advance"] * n)
    other_blocker = stalled(3, ["v1"] * n, ["did not advance"] * (n - 1) + ["field required"])
    # The daily cap (PERSIST_DAILY) is a separate guard: these runs are over a day old.
    await persist_stalled(service, 10, [], {})
    assert looping not in resumed
    assert {new_build, other_blocker} <= set(resumed)
