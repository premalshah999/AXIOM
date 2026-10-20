"""A saved rejection must not outlive the owner's admission policy."""
from jobpilot import autopilot
from jobpilot.admission import QueueBlocked
from jobpilot.application_pacing import LIMIT_KEY
from jobpilot.db import Job
from jobpilot.jobboard import BoardSettings


async def test_raising_company_limit_reconsiders_ready_once(service, monkeypatch):
    service.dispatch = lambda *args: None
    service.db.set_setting("control", {"auto_submit": True})
    service.db.set_setting(LIMIT_KEY, {"limit": 6})
    cfg = BoardSettings(auto_apply=True)
    monkeypatch.setattr(autopilot, "settings", lambda db: cfg)
    monkeypatch.setattr(autopilot, "replenish_discovery", lambda *args: None)
    async def no_retries(*args):
        return 0
    monkeypatch.setattr(autopilot, "persist_stalled", no_retries)
    with service.db.exclusive() as s:
        job = Job(identity="policy-ready", url="https://employer.example/job/1", ats="oracle",
                  title="Software Engineer", company="Example", status="ready", score=90)
        s.add(job)
        s.flush()
        jid = job.id
    board = {"jobs": [{"id": jid, "status": "ready", "score": 90,
                      "title": "Software Engineer", "company": "Example", "location": "US",
                      "posted_at": "2026-10-17", "career_level": "New grad"}]}
    monkeypatch.setattr(autopilot, "board_snapshot", lambda *args, **kwargs: board)
    calls = []
    async def queue(job_id, *args, **kwargs):
        calls.append(job_id)
        if service.db.get_setting(LIMIT_KEY)["limit"] == 6:
            raise QueueBlocked("Employer limit 6", code="company_limit")
        return {"id": "queued-test"}
    monkeypatch.setattr(service, "queue", queue)
    await autopilot.tick(service)
    await autopilot.tick(service)
    assert calls == [jid]  # No busy rechecking of the same rejection.
    service.db.set_setting(LIMIT_KEY, {"limit": 12})
    await autopilot.tick(service)
    await autopilot.tick(service)
    assert calls == [jid, jid]
    assert service.db.get_setting("job_board_autopilot")["queued"] == 1


def test_admission_revision_ignores_unrelated_settings(service):
    cfg = BoardSettings()
    before = autopilot.admission_revision(service.db, cfg)
    service.db.set_setting("application_pacing:company_limit", {"reason": "New description"})
    service.db.set_setting("profile", {"city": "Different city"})
    assert autopilot.admission_revision(service.db, cfg) == before
    service.db.set_setting("ats_focus", {"exclude": ["workday"]})
    assert autopilot.admission_revision(service.db, cfg) != before
