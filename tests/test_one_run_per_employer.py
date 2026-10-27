"""Seven Amazon retries signed in to one account together and locked it out (2026-10-27): one application per
employer runs at a time, and a lockout after refused sign-ins is recovered by a mailbox reset."""

import pytest

from jobpilot.adapters.base import LOCKED
from jobpilot.admission import QueueBlocked
from jobpilot.db import Job, Run


def test_a_second_application_at_a_busy_employer_waits(service):
    with service.db.exclusive() as s:
        for jid in ("a1", "a2"):
            s.add(Job(id=jid, identity=jid, url=f"https://www.amazon.jobs/jobs/{jid}", ats="custom", status="ready"))
        s.flush()
        s.add(Run(id="run-a1", job_id="a1", state="running", packet={}))
    with service.db.session() as s:
        with pytest.raises(QueueBlocked) as raised:
            service.check_cooldown(s, s.get(Job, "a2"))
        assert raised.value.code == "employer_busy"
        service.check_cooldown(s, s.get(Job, "a1"))  # its own run does not hold it


def test_amazons_lockout_wording_is_a_lockout():
    assert LOCKED.search("Sorry, you can't log in right now due to unsuccessful login attempts.")
    assert not LOCKED.search("Log in to continue your application")
