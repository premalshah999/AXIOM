"""A sender-busy or browser-unavailable wait sent nothing, so a carried-over repair count must not strand it."""

import pytest

from jobpilot.admission import QueueBlocked
from jobpilot.db import Job, Run, Setting


def seed_exhausted_recovery(service, cause):
    service.db.set_setting("control", {"paused": False, "auto_submit": True})
    service.db.set_setting("profile", {"autonomous": True})
    with service.db.session() as s:
        s.add_all(
            [
                Job(
                    id="job",
                    identity="job",
                    url="https://careers.example.test/job/1",
                    ats="oracle",
                    status="timed_out",
                ),
                Run(
                    id="source",
                    job_id="job",
                    state="timed_out",
                    mode="submit",
                    reason="waiting",
                    packet={"match_approved": True, "profile": {"autonomous": True}},
                ),
                # Three repair attempts were spent earlier; the budget is two.
                Setting(
                    key="draft_recovery:job",
                    value={
                        "source_run_id": "source",
                        "scheduled": True,
                        "cause": cause,
                        "attempts": 3,
                        "attempt_limit": 2,
                    },
                ),
            ]
        )


@pytest.mark.parametrize("cause", ["sender_busy", "browser_unavailable", "worker_interrupted"])
async def test_lease_wait_is_not_stranded_by_exhausted_repair_budget(service, cause):
    seed_exhausted_recovery(service, cause)
    # The guard is passed when queueing reaches the resume lookup, before any run is created.
    with pytest.raises(ValueError, match="Choose an uploaded resume") as exc:
        await service.queue("job", "submit", retry=True, recovery_source_run="source")
    assert not isinstance(exc.value, QueueBlocked)


async def test_exhausted_repair_budget_still_blocks_a_repair_cause(service):
    seed_exhausted_recovery(service, "model_output")
    with pytest.raises(QueueBlocked) as blocked:
        await service.queue("job", "submit", retry=True, recovery_source_run="source")
    assert blocked.value.code == "stale_recovery"


def test_deploy_continuation_is_immediate_even_after_repairs_exhausted(service):
    from jobpilot.recovery import schedule_recovery, automatic_attempts, specialist_attempts
    seed_exhausted_recovery(service, "worker_interrupted")
    with service.db.exclusive() as session:
        source = session.get(Run, "source")
        schedule_recovery(session, service.config, source, {"recovery_cause": "worker_interrupted"})
        saved = session.get(Setting, "draft_recovery:job").value
        assert saved["scheduled"] and saved["attempts"] == 3
        assert saved["not_before"] <= saved["updated_at"]
        for pool in ("normal", "specialist"):
            session.add(Run(job_id="job", mode="submit", packet={"source_run_id":"source",
                "worker_pool":pool,"recovery_continuation_cause":"worker_interrupted"}))
        session.flush()
        assert automatic_attempts(session,"job") == 0
        assert specialist_attempts(session,"job") == 0
