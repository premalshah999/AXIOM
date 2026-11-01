"""A disk or quota that fills under a run is the machine's trouble, not the application's (soak, 2026-11-01).

Writing the run folder failed with ENOSPC; the generic handler stored "Worker error: OSError", and 47 runs failed in a minute,
every job ending as Couldn't apply after four attempts. Before the commit it is a free wait; during it, an unknown submission."""
import asyncio
import errno

import pytest
from sqlalchemy import select

from jobpilot import host_health, pipeline_comparison, service as service_module
from jobpilot.application_budget import nothing_sent
from jobpilot.db import Job, Run


def _run_with_engine_error(service, monkeypatch, run_state, exc):
    class FullDiskEngine:
        result = None

        async def run(self, job, run):
            if run_state == "submitting":  # the commit had begun when the write failed
                with service.db.exclusive() as s:
                    s.get(Run, "run-full").state = "submitting"
            raise exc

    with service.db.exclusive() as s:
        s.add(Job(id="job", identity="job", url="https://fixture.test/job", status="new"))
        s.add(Run(id="run-full", job_id="job", state="queued", mode="dry_run", packet={}))
    monkeypatch.setattr(pipeline_comparison, "engine_for", lambda *a, **k: FullDiskEngine())
    monkeypatch.setattr(pipeline_comparison, "slots", lambda *a, **k: ["owner-1"])
    monkeypatch.setattr(service_module, "may_start", lambda *a, **k: True, raising=False)
    asyncio.run(service._execute_owned("run-full"))
    with service.db.session() as s:
        return s.scalars(select(Run).where(Run.id == "run-full")).one()


@pytest.mark.parametrize("code", [errno.ENOSPC, errno.EDQUOT])
def test_a_full_disk_before_the_commit_waits_and_sends_nothing(service, monkeypatch, code):
    run = _run_with_engine_error(service, monkeypatch, "queued", OSError(code, "No space left on device"))
    assert run.state == "waiting_browser"
    assert nothing_sent(run.state, run.reason)
    assert run.reason.startswith("The disk is full")
    assert host_health.disk.low() == 0  # nothing new starts for the next half minute


def test_a_full_disk_under_a_commit_is_an_unknown_submission_never_a_failure(service, monkeypatch):
    run = _run_with_engine_error(service, monkeypatch, "submitting", OSError(errno.ENOSPC, "No space left on device"))
    assert run.state == "submission_unknown"
    assert "not sent again" in run.reason


def test_the_cause_chain_counts_and_other_errors_still_fail(service, monkeypatch):
    try:
        try:
            raise OSError(errno.ENOSPC, "No space left on device")
        except OSError as inner:
            raise RuntimeError("could not save the page") from inner
    except RuntimeError as wrapped:
        assert host_health.disk_full(wrapped)
    assert not host_health.disk_full(PermissionError(errno.EACCES, "denied"))
    run = _run_with_engine_error(service, monkeypatch, "queued", PermissionError(errno.EACCES, "denied"))
    assert run.state == "failed"


def test_a_small_runs_volume_is_judged_by_a_tenth_of_its_size_not_the_two_gigabyte_floor(tmp_path, monkeypatch):
    """A runs folder linked to a 64 MB quota kept the gate shut for good in the soak: its free space was never over 2 GB."""
    usage = {"data": (50 * 1024**3, 500 * 1024**3), "runs": (60 * 1024**2, 64 * 1024**2)}

    def disk_usage(path):
        free, total = usage["runs" if str(path).endswith("runs") else "data"]
        return type("U", (), {"free": free, "total": total})()

    monkeypatch.setattr(host_health.shutil, "disk_usage", disk_usage)
    watch = host_health.DiskWatch()
    watch.watch(tmp_path)
    assert watch.low() is None
    usage["runs"] = (2 * 1024**2, 64 * 1024**2)  # the quota filled
    watch.watch(tmp_path)
    assert watch.low() == 2 * 1024**2
