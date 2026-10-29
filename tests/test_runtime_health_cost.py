"""/api/runtime-health must not re-read hundreds of ~320 KB run packets on every poll (2026-10-29)."""

from sqlalchemy import event

from jobpilot.db import Job, Run


def seed_confirmed(service, count, *, packet=True, email=False):
    with service.db.session() as s:
        for n in range(count):
            jid = f"job-{n}"
            s.add(Job(id=jid, identity=jid, url=f"https://fixture.test/job/{n}"))
            receipt = {
                "type": "explicit_confirmation_page",
                "evidence_version": 2,
                "website_confirmed": True,
                "job_id": jid,
                "run_id": f"run-{n}",
                "evidence_selector": "h1",
                "status_region": "main",
                "confirmation": "Received",
                "resume_sha256": "pinned",
            }
            if email:
                receipt = {
                    "email": {
                        "authenticated": True,
                        "sender_trusted": True,
                        "message_id": f"m{n}",
                        "job_id": jid,
                    }
                }
            s.add(
                Run(
                    id=f"run-{n}",
                    job_id=jid,
                    state="confirmed",
                    created_at="2026-10-12T01:00:00+00:00",
                    receipt=receipt,
                    packet={"resume_sha": "pinned"} if packet else {},
                )
            )


def packet_reads(service, call):
    seen = []

    def watch(conn, cursor, statement, *rest):
        if "packet" in statement:
            seen.append(statement)

    event.listen(service.db.engine, "before_cursor_execute", watch)
    try:
        result = call()
    finally:
        event.remove(service.db.engine, "before_cursor_execute", watch)
    return result, len(seen)


def set_target(service):
    from jobpilot.autopilot import PRODUCTION_TARGET

    service.db.set_setting(
        PRODUCTION_TARGET, {"started_at": "2026-10-12T00:00:00+00:00", "target": 100, "baseline_run_ids": []}
    )


def test_production_progress_reads_each_packet_once(service):
    from jobpilot import autopilot

    autopilot._RESUME_SHA.clear()
    set_target(service)
    seed_confirmed(service, 450)  # three batches of 200 at most
    first, reads = packet_reads(service, lambda: autopilot.production_progress(service.db))
    assert first["confirmed"] == 450 and 1 <= reads <= 3
    again, reads = packet_reads(service, lambda: autopilot.production_progress(service.db))
    assert again == first and reads == 0  # remembered: the 30 s poll no longer parses 2,500 packets


def test_a_trusted_email_receipt_needs_no_packet_at_all(service):
    from jobpilot import autopilot

    autopilot._RESUME_SHA.clear()
    set_target(service)
    seed_confirmed(service, 5, packet=False, email=True)
    result, reads = packet_reads(service, lambda: autopilot.production_progress(service.db))
    assert result["confirmed"] == 5 and reads == 0


def test_a_website_receipt_for_another_resume_still_does_not_count(service):
    from jobpilot import autopilot

    autopilot._RESUME_SHA.clear()
    set_target(service)
    seed_confirmed(service, 3)
    with service.db.session() as s:
        s.get(Run, "run-1").packet = {"resume_sha": "another"}
        s.get(Run, "run-2").packet = {}
    assert autopilot.production_progress(service.db)["confirmed"] == 1
