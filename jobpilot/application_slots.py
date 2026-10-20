"""Durable browser-owner identities; Telegram is an optional view of them."""

from .db import Setting, now


def claim_slot(session, run, limit=8):
    # Called inside the same exclusive transaction as queued -> running.
    from .pipeline_comparison import NORMAL, normalize_ats, pool_for, slots
    from .db import Job

    available = slots(session, run, limit)
    if not available:
        raise RuntimeError("No application-owner slot is available")
    preferred = run.packet.get("worker_slot")
    slot = preferred if preferred in available else available[0]
    job = session.get(Job, run.job_id)
    pool = pool_for(run)
    specialist = normalize_ats(job.ats) if job else run.packet.get("ats_specialist", "custom")
    run.packet = {**run.packet, "worker_slot": slot, "worker_pool": pool, "ats_specialist": specialist}
    key = f"telegram_worker_slot:{slot}"
    value = {
        "run_id": run.id,
        "job_id": run.job_id,
        "state": "running",
        "updated_at": now(),
        "pool": pool,
        "ats_specialist": specialist,
        "reserved_difficult_flow": pool != NORMAL,
    }
    row = session.get(Setting, key)
    if row:
        row.value = value
    else:
        session.add(Setting(key=key, value=value))
    return slot


def release_slot(session, run):
    slot = run.packet.get("worker_slot")
    row = session.get(Setting, f"telegram_worker_slot:{slot}") if slot else None
    if row and row.value.get("run_id") == run.id:
        row.value = {**row.value, "state": run.state, "updated_at": now()}
