"""A site's sign-in lockout is waited out (Amazon 2026-10-27: "Sorry, you can't log in right now due to unsuccessful login
attempts", then fourteen more sign-ins over two hours). The wait is a real one for every job at that employer: the
admission circuit it writes holds across a deploy, which the build-tied circuit of a recurring failure does not."""

import hashlib
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from jobpilot.adapters.base import Adapter, lockout_wait
from jobpilot.admission import QueueBlocked, employer_partition
from jobpilot.db import Job, Run
from jobpilot.recovery import schedule_recovery

LOCK = "Sorry, you can't log in right now due to unsuccessful login attempts."


def adapter():
    a = object.__new__(Adapter)
    a.release_mail = lambda: None
    return a


def obs(**kw):
    return {"errors": [], "text": "", "headings": [], "dialogs": [], "controls": [], "fields": [], **kw}


def test_a_lock_notice_is_a_cooldown_result():
    for page in (obs(errors=[LOCK]), obs(text="Welcome\n" + LOCK + "\nBack")):
        result = adapter().site_lock(page)
        assert result["recovery_cause"] == "site_temporary" and result["site_cooldown_seconds"] == 3600
        assert result["state"] == "waiting_browser" and "no further attempt" in result["reason"]


@pytest.mark.parametrize("text", [
    "Sign in to continue your application",
    "Accounts will be locked after 5 failed sign in attempts.",       # a policy line, not a lock
    "Too many open positions match your search",
])
def test_help_text_and_unrelated_wording_is_not_a_lock(text):
    assert adapter().site_lock(obs(text=text)) is None


def test_a_lock_a_password_reset_lifts_stays_the_account_flows():
    page = obs(errors=["Your account has been blocked after multiple consecutive login attempts. Reset your password to "
                       "unblock your account."], text="too many attempts")
    assert adapter().site_lock(page) is None


def test_the_wait_the_page_names_is_used():
    assert lockout_wait("Try again in 30 minutes") == 30 * 60 + 300
    assert lockout_wait("try again after 2 hours") == 2 * 3600 + 300
    assert lockout_wait("try again in 1 minute") == 600
    assert lockout_wait("try again in 99 hours") == 24 * 3600
    assert lockout_wait(LOCK) == 3600


def test_the_circuit_a_lockout_writes_holds_other_jobs_at_that_employer(service):
    url = "https://careers.example.test/jobs/1"
    with service.db.exclusive() as s:
        for n in ("a", "b"):
            s.add(Job(id=n, identity=n, url=url + n, ats="custom", company="ExampleSoft", status="ready"))
        run = Run(id="r1", job_id="a", mode="submit", state="waiting_browser",
                  packet={"match_approved": True, "profile": {"autonomous": True}})
        s.add(run)
        s.flush()
        from jobpilot.adapters.base import lockout_result

        schedule_recovery(s, service.config, run, lockout_result(LOCK))
    key = "site_circuit:" + hashlib.sha256(employer_partition({"url": url + "b", "ats": "custom"}).encode()).hexdigest()
    circuit = service.db.get_setting(key, {})
    assert "engine" not in circuit and datetime.fromisoformat(circuit["until"]) - datetime.now(UTC) > timedelta(minutes=59)
    with service.db.session() as s:
        with pytest.raises(QueueBlocked):
            service.check_cooldown(s, s.get(Job, "b"))
