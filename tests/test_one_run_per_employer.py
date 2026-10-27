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


def test_a_site_circuit_holds_only_the_build_that_tripped_it(service):
    import hashlib
    from datetime import UTC, datetime, timedelta

    from jobpilot.admission import employer_partition
    from jobpilot.runtime import engine_version

    with service.db.exclusive() as s:
        s.add(Job(id="c1", identity="c1", url="https://workforcenow.adp.com/x?cid=1", ats="adp", status="ready"))
    key = "site_circuit:" + hashlib.sha256(employer_partition({"url": "https://workforcenow.adp.com/x?cid=1",
                                                                 "ats": "adp"}).encode()).hexdigest()
    until = (datetime.now(UTC) + timedelta(hours=5)).isoformat()
    service.db.set_setting(key, {"until": until, "reason": "3 stopped", "engine": "older-build"})
    with service.db.session() as s:
        service.check_cooldown(s, s.get(Job, "c1"))  # a deploy released it
    service.db.set_setting(key, {"until": until, "reason": "3 stopped", "engine": engine_version()})
    with service.db.session() as s:
        with pytest.raises(QueueBlocked):
            service.check_cooldown(s, s.get(Job, "c1"))
