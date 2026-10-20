from datetime import datetime
import pytest
from jobpilot.db import Job, Run, Setting
from jobpilot.recovery import schedule_recovery


@pytest.mark.parametrize("cause,attempts,expected", [
    ("control_observation", 0, 30),
    ("validation_controls", 1, 60),
    ("navigation_stalled", 0, 30),
    ("site_temporary", 0, 120),
    ("site_temporary", 1, 240),
])
def test_local_repair_delay_fits_budget_without_shortening_outages(service, cause, attempts, expected):
    service.config.recovery_delay_seconds = 120
    with service.db.exclusive() as session:
        session.add(Job(id="delay-job", identity="delay-job", url="https://employer.example/jobs/1", ats="icims"))
        run = Run(id="delay-run", job_id="delay-job", mode="submit", state="timed_out",
                  packet={"match_approved": True, "profile": {"autonomous": True}})
        session.add(run)
        session.add(Setting(key="draft_recovery:delay-job", value={"attempts": attempts}))
        session.flush()
        schedule_recovery(session, service.config, run, {"recovery_cause": cause})
        saved = session.get(Setting, "draft_recovery:delay-job").value
        wait = (datetime.fromisoformat(saved["not_before"]) - datetime.fromisoformat(saved["updated_at"])).total_seconds()
        assert expected - .1 <= wait <= expected + .1
        assert saved["scheduled"]
