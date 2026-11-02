"""A person who sent consent but never turned on "choose new jobs by itself" keeps recovery for work they approved.

The profile switch `autonomous` is about picking new jobs. A job the person approved that was cut off by a crash, an update,
a missing browser, a model outage or an overloaded host sent nothing, so it continues whatever the switch says. A repair
(the engine changing what it does) stays behind the switch."""

import pytest

from jobpilot.admission import QueueBlocked
from jobpilot.db import Job, Run, Setting
from jobpilot.recovery import schedule_recovery

WAITS = ["worker_interrupted", "browser_unavailable", "provider_unavailable", "host_overloaded", "sender_busy"]


def seed(service, autonomous, cause=None):
    service.db.set_setting("control", {"paused": False, "auto_submit": True})
    service.db.set_setting("profile", {"autonomous": autonomous})
    with service.db.session() as s:
        s.add_all(
            [
                Job(id="job", identity="job", url="https://careers.example.test/job/1", ats="oracle", status="timed_out"),
                Run(
                    id="source",
                    job_id="job",
                    state="timed_out",
                    mode="submit",
                    reason="waiting",
                    packet={"match_approved": True, "profile": {"autonomous": autonomous}},
                ),
            ]
        )
        if cause:
            s.add(
                Setting(
                    key="draft_recovery:job",
                    value={"source_run_id": "source", "scheduled": True, "cause": cause, "attempts": 0, "not_before": "2000-01-01"},
                )
            )


def schedule(service, cause):
    with service.db.exclusive() as session:
        schedule_recovery(session, service.config, session.get(Run, "source"), {"recovery_cause": cause})
    return service.db.get_setting("draft_recovery:job", {})


@pytest.mark.parametrize("cause", WAITS)
@pytest.mark.parametrize("autonomous", [True, False])
def test_waits_are_scheduled_with_default_flags(service, cause, autonomous):
    seed(service, autonomous)
    assert schedule(service, cause).get("scheduled") is True


def test_repair_cause_still_needs_the_switch(service):
    seed(service, False)
    assert not schedule(service, "model_output").get("scheduled")
    service.db.set_setting("draft_recovery:job", {})


@pytest.mark.parametrize("cause", WAITS)
@pytest.mark.parametrize("autonomous", [True, False])
async def test_waits_are_admitted_with_default_flags(service, cause, autonomous):
    seed(service, autonomous, cause)
    with pytest.raises(ValueError, match="Choose an uploaded resume") as exc:
        await service.queue("job", "submit", retry=True, recovery_source_run="source")
    assert not isinstance(exc.value, QueueBlocked)


async def test_repair_cause_is_not_admitted_without_the_switch(service):
    seed(service, False, "model_output")
    with pytest.raises(QueueBlocked) as blocked:
        await service.queue("job", "submit", retry=True, recovery_source_run="source")
    assert blocked.value.code == "stale_recovery"
