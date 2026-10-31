"""A run that stopped on the reset cooldown sent nothing and is retried when the cooldown can have ended (2026-10-31).

11 runs on 7 jobs ended 'A password reset was requested recently' and went through the generic repair path: an attempt spent
each time, then 'unchanged' and parked. It is a wait like a busy sender: no attempt spent, a delay that grows."""

from datetime import datetime

from jobpilot.db import Job, Run, Setting
from jobpilot.recovery import RESET_COOLDOWN, WAIT_BACKOFF, automatic_attempts, schedule_recovery


def test_a_reset_cooldown_wait_is_scheduled_without_spending_an_attempt(service):
    with service.db.exclusive() as s:
        s.add(Job(id="job", identity="job", url="https://careers.acme.test/1", ats="avature"))
        run = Run(id="run", job_id="job", mode="submit", state="waiting_browser",
                  reason=RESET_COOLDOWN + "; awaiting delivery", packet={"match_approved": True, "profile": {"autonomous": True}})
        s.add(run)
        s.flush()
        schedule_recovery(s, service.config, run, {"recovery_cause": "reset_cooldown"})
        saved = s.get(Setting, "draft_recovery:job").value
        first = WAIT_BACKOFF["reset_cooldown"][0]
        assert saved["status"] == "reset_cooldown" and saved["scheduled"] and saved["sender_waits"] == 1
        assert abs(datetime.fromisoformat(saved["not_before"]).timestamp() - datetime.now().timestamp()) < first + 5 + 3600 * 24
        assert first >= 600  # not a retry every few seconds into the same cooldown
        # And it is not counted as a repair attempt of this job.
        s.add(Run(id="retry", job_id="job", mode="submit", state="waiting_browser", reason=RESET_COOLDOWN + "; awaiting delivery",
                  packet={"source_run_id": "run"}))
        s.flush()
        assert automatic_attempts(s, "job") == 0
