"""A run the app cancelled before anything reached the employer is no attempt (2026-10-31: 43 Ready jobs sat idle for a
week with every worker free; the autopilot counted any run, cancelled or not, as an attempt)."""
from types import SimpleNamespace

from jobpilot import autopilot, execution_services
from jobpilot.admission import HOLD_WAIT, attempted_job_ids
from jobpilot.db import Job, Run, Setting
from jobpilot.jobboard import BoardSettings

MAINTENANCE = "Owner cancelled saved application during maintenance"


def add_job(service, key, *, status="ready"):
    with service.db.exclusive() as s:
        job = Job(identity="cancel-" + key, url=f"https://{key}.example.test/job/12345", ats="oracle",
                  title="Software Engineer", company="Example " + key, location="Remote", status=status, score=90,
                  description="Build and ship software. " * 40)
        s.add(job)
        s.flush()
        return job.id


def add_run(service, job_id, state="cancelled", *, reason="", started=None, receipt=None):
    with service.db.exclusive() as s:
        run = Run(job_id=job_id, state=state, mode="submit", reason=reason, started_at=started, receipt=receipt or {},
                  packet={})
        s.add(run)
        s.flush()
        return run.id


def cases(service):
    """One job per kind of cancelled run; the value says whether it must still count as an attempt."""
    kinds = {
        # The app's own cancellations, before anything was sent.
        "queued_cancel": (dict(), False),  # POST /api/runs/<id>/cancel, 2026-10-23: no reason, never started
        "maintenance_sweep": (dict(reason=MAINTENANCE, started="2026-10-07T10:00:00+00:00"), False),
        "superseded": (dict(reason="Replaced by a newer attempt; the email wait ended (was: waiting_email)",
                            started="2026-10-07T10:00:00+00:00"), False),
        "reserved_hold": (dict(reason="Apple is a reserved employer (FAANG)" + HOLD_WAIT), False),
        # A verdict on the job, or no way to tell: final.
        "ssn_rule": (dict(reason="Application asks for ssn; the job was removed under the owner rule",
                          started="2026-10-07T10:00:00+00:00"), True),
        "owner_deleted": (dict(reason="Owner deleted this application", started="2026-10-07T10:00:00+00:00"), True),
        "started_without_reason": (dict(started="2026-10-07T10:00:00+00:00"), True),
        "unknown_reason_never_started": (dict(reason="Something else cancelled this"), True),
        "receipt": (dict(receipt={"website_confirmed": False, "note": "x"}), True),
    }
    ids = {}
    for kind, (fields, _) in kinds.items():
        ids[kind] = add_job(service, kind)
        add_run(service, ids[kind], **fields)
    return ids, {kind: final for kind, (_, final) in kinds.items()}


def test_only_the_apps_own_cancellations_before_a_send_leave_a_job_unattempted(service):
    ids, final = cases(service)
    # A commit may have reached the employer: a `submitting` event on the cancelled run keeps it final.
    ids["commit_started"] = add_job(service, "commit_started")
    run = add_run(service, ids["commit_started"], reason=MAINTENANCE, started="2026-10-07T10:00:00+00:00")
    service.db.event(run, "submitting", "Commit step started; automatic retries are disabled", {})
    # The owner removed the job: its queued run was cancelled with no reason, and the removal stays.
    ids["owner_removed"] = add_job(service, "owner_removed")
    add_run(service, ids["owner_removed"])
    service.db.set_setting("job_control:" + ids["owner_removed"], {"state": "deleted"})
    # Any other run of the job still counts; a closed posting is not a cancellation.
    ids["also_failed"] = add_job(service, "also_failed")
    add_run(service, ids["also_failed"])
    add_run(service, ids["also_failed"], "failed", reason="boom", started="2026-10-07T10:00:00+00:00")
    ids["closed"] = add_job(service, "closed")
    add_run(service, ids["closed"], "closed", reason="This posting is closed")
    ids["never_run"] = add_job(service, "never_run")
    final |= {"commit_started": True, "owner_removed": True, "also_failed": True, "closed": True, "never_run": False}
    with service.db.session() as s:
        counted = attempted_job_ids(s)
        assert {kind for kind, job_id in ids.items() if job_id in counted} == {k for k, v in final.items() if v}
        # The same answer for one job at a time (the autopilot's screen) and for a chosen set.
        assert all((job_id in attempted_job_ids(s, [job_id])) == final[kind] for kind, job_id in ids.items())
        assert attempted_job_ids(s, []) == set()


def test_a_ready_job_with_only_a_queued_cancel_is_listed_as_a_candidate_without_a_listing(service):
    free, removed, sent = (add_job(service, kind) for kind in ("free", "removed", "sent"))
    add_run(service, free)
    add_run(service, removed, reason="Owner deleted this application", started="2026-10-07T10:00:00+00:00")
    add_run(service, sent, "confirmed", started="2026-10-07T10:00:00+00:00")
    found = {j["id"] for j in autopilot.unlisted_ready(service.db, SimpleNamespace(max_age_hours=720), set())}
    assert found == {free}


async def run_tick(service, monkeypatch, queue):
    service.dispatch = lambda *args: None
    service.db.set_setting("control", {"auto_submit": True})
    service.db.set_setting("execution_services", {"enabled": True, "version": 1})
    service.db.set_setting("application_resume_policy", {"mode": "master"})
    cfg = BoardSettings(auto_apply=True)
    monkeypatch.setattr(autopilot, "settings", lambda db: cfg)
    monkeypatch.setattr(autopilot, "replenish_discovery", lambda *args: None)

    async def nothing(*args, **kwargs):
        return 0

    async def prepared(service):
        return SimpleNamespace(classify=[])

    monkeypatch.setattr(autopilot, "persist_stalled", nothing)
    monkeypatch.setattr(autopilot, "resubmit_unknown", nothing)
    monkeypatch.setattr(execution_services, "prepare_due", prepared)
    monkeypatch.setattr(service, "queue", queue)
    await autopilot.tick(service)
    return service.db.get_setting("job_board_autopilot")


async def test_the_autopilot_starts_a_job_whose_run_was_cancelled_before_it_sent_anything(service, monkeypatch):
    """Workable jobs whose queued runs were cancelled to unblock a deploy (2026-10-23) were never started again."""
    cancelled, maintenance, removed, ssn = (add_job(service, kind) for kind in ("cancelled", "maint", "removed", "ssn"))
    add_run(service, cancelled)
    add_run(service, maintenance, reason=MAINTENANCE, started="2026-10-07T10:00:00+00:00")
    add_run(service, removed)
    service.db.set_setting("job_control:" + removed, {"state": "deleted"})
    add_run(service, ssn, reason="Application asks for ssn; the job was removed under the owner rule",
            started="2026-10-07T10:00:00+00:00")
    called = []

    async def queue(job_id, *args, **kwargs):
        called.append(job_id)
        return {"id": "run-" + job_id}

    await run_tick(service, monkeypatch, queue)
    assert sorted(called) == sorted([cancelled, maintenance])
    with service.db.session() as s:
        assert s.get(Setting, "job_control:" + removed).value["state"] == "deleted"
