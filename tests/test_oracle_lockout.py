"""Oracle locks email verification for 30 minutes after too many attempts ("Too Many Attempts. Try Again Later.").
Fortinet's tenant was locked by the 10-17 retry storm, and on 10-18 the navigator waited on the notice. The lock now
ends the attempt as a site-named wait: the shared sender is released, and the retry and the employer's site circuit
hold for 35 minutes."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from jobpilot.adapters.base import Step
from jobpilot.adapters.oracle import LOCK_SECONDS, Oracle
from jobpilot.db import Job, Run, Setting
from jobpilot.recovery import schedule_recovery

LOCKED_PAGE = {
    "url": "https://edel.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_2001/job/24671/apply/email",
    "text": "Too Many Attempts. Try Again Later. You reached the maximum number of attempts. Try again in 30 minutes.",
    "fields": [],
    "controls": [],
}


def test_the_lock_releases_the_sender_and_names_its_wait():
    released = []
    oracle = object.__new__(Oracle)
    oracle.committed = False
    oracle.release_mail = lambda: released.append(True)
    assert oracle.classify_page(LOCKED_PAGE) == Step.UNKNOWN
    result = oracle.site_lock(LOCKED_PAGE)
    assert result["recovery_cause"] == "site_temporary" and result["site_cooldown_seconds"] == LOCK_SECONDS
    assert released == [True]
    assert oracle.site_lock({**LOCKED_PAGE, "text": "Enter the verification code we sent"}) is None


def test_recovery_waits_out_the_lock_for_this_employer(service):
    with service.db.exclusive() as s:
        s.add(Job(id="fortinet", identity="fortinet", url=LOCKED_PAGE["url"], ats="oracle", company="Fortinet"))
        run = Run(id="locked", job_id="fortinet", mode="submit", state="waiting_browser",
                  packet={"match_approved": True, "profile": {"autonomous": True}})
        s.add(run)
        s.flush()
        schedule_recovery(s, service.config, run, Oracle.site_lock(
            SimpleNamespace(release_mail=lambda: None), LOCKED_PAGE))
        saved = s.get(Setting, "draft_recovery:fortinet").value
        held = datetime.fromisoformat(saved["not_before"]) - datetime.now(UTC)
        assert saved["scheduled"] and held > timedelta(minutes=34)
        circuits = [r.value for r in s.query(Setting).filter(Setting.key.like("site_circuit:%"))]
        assert len(circuits) == 1 and datetime.fromisoformat(circuits[0]["until"]) - datetime.now(UTC) > timedelta(minutes=34)


async def test_keep_me_signed_in_is_ticked_when_the_employer_offers_it():
    # Goldman Sachs and five other tenants allow it; later applications there then skip the emailed code.
    filled, events = [], []

    async def fill(field, value):
        filled.append((field["label"], value))

    oracle = object.__new__(Oracle)
    oracle.e = SimpleNamespace(form=SimpleNamespace(fill=fill))
    oracle.emit = lambda *args: events.append(args[0])
    await oracle.keep_signed_in({"fields": [
        {"type": "checkbox", "label": "Keep me signed in", "value": ""},
        {"type": "checkbox", "label": "I agree to receive job alerts", "value": ""},
        {"type": "text", "label": "Enter verification code digit 1 of six.", "value": ""},
    ]})
    assert filled == [("Keep me signed in", "true")] and events == ["account"]
    filled.clear()
    await oracle.keep_signed_in({"fields": [{"type": "checkbox", "label": "Keep me signed in", "value": "true"}]})
    assert filled == []
