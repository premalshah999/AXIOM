"""One copy of each profile version, instead of one per run.

A run must use exactly the profile it started with, so its packet carried a full copy: about 330 KB of a 490 KB packet
(`reviewed_answers` alone is 270 KB, 2026-10-30), most of the 1.4 GB `runs.packet` takes on disk. Most runs share a
version: 13 among the latest 300. The copy now lives once in `profile_snapshots`, keyed by its content hash, and the
packet keeps the hash plus the few fields read by SQL or for every finished run (INLINE). Nothing changes or deletes a
snapshot, so a run can always rebuild the exact profile with `full`.

Packets from before this change hold the whole profile and still read as they always did.
"""

import json
from collections import OrderedDict

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from .db import ProfileSnapshot, Run, now
from .runtime import profile_version

# Read straight from the packet: receipts match on the applicant's address (also in SQL), recovery asks `autonomous`.
INLINE = ("email", "autonomous")
# Every snapshot is immutable, so remembering one is always right. A few are in use at any moment; each is 330 KB.
_REMEMBERED = OrderedDict()
_REMEMBER = 8


def _inline(profile):
    return {key: profile[key] for key in INLINE if key in profile}


def _exists(session, version):
    # Only the key: the row is 330 KB.
    return session.scalar(select(ProfileSnapshot.version).where(ProfileSnapshot.version == version)) is not None


def _store(session, version, profile):
    # Compaction and the queue can reach one new version together; the second writer has nothing to add.
    insert = postgresql_insert if session.get_bind().dialect.name == "postgresql" else sqlite_insert
    session.execute(
        insert(ProfileSnapshot).values(version=version, profile=profile, created_at=now()).on_conflict_do_nothing()
    )


def keep(session, profile):
    """Store `profile` once. Returns the packet's fields for it: the inline part and the hash that finds the rest."""
    version = profile_version(profile)
    if not _exists(session, version):
        _store(session, version, profile)
    return {"profile": _inline(profile), "profile_snapshot": version}


def full(packet, source):
    """The profile the run started with. `source` is a Database or an open session."""
    packet = packet or {}
    version = packet.get("profile_snapshot")
    if not version:
        return packet.get("profile") or {}  # A packet that still holds its own copy.
    text = _REMEMBERED.get(version)
    if text is None:
        if hasattr(source, "session"):
            with source.session() as session:
                row = session.get(ProfileSnapshot, version)
                value = row.profile if row else None
        else:
            row = source.get(ProfileSnapshot, version)
            value = row.profile if row else None
        if value is None:
            # Never fall back to the current profile: the run would answer with facts it did not start with.
            raise RuntimeError(f"Profile snapshot {version} is missing")
        text = json.dumps(value)
        _REMEMBERED[version] = text
        while len(_REMEMBERED) > _REMEMBER:
            _REMEMBERED.popitem(last=False)
    return json.loads(text)


# States a run never leaves and that nothing resumes from. recovery._eligible_specialist_history reads the checkpoints
# of waiting_browser, timed_out and failed runs, pipeline_comparison.CONTINUABLE_STATES names what a new run continues,
# and a submission_unknown may still be retried. A job's next run after any state below starts with no checkpoint.
NEVER_RESUMED = frozenset({"confirmed", "already_applied", "closed", "cancelled", "dry_run_passed", "skipped"})
ACTIVE = frozenset({"queued", "running", "submitting"})


def slimmed(run_state, packet, keeps_checkpoint=False):
    """(packet without what is redundant, profile to store or None). `packet` is not changed."""
    profile = None
    new = dict(packet)
    if "profile_snapshot" not in packet and isinstance(packet.get("profile"), dict) and packet["profile"]:
        profile = packet["profile"]
        new["profile"], new["profile_snapshot"] = _inline(profile), profile_version(profile)
    if run_state in NEVER_RESUMED and not keeps_checkpoint and "browser_checkpoint" in new:
        del new["browser_checkpoint"]
    return new, profile


def compact_runs(db, *, apply=False, batch=50, pause=0.02, progress=None, stop=None) -> dict:
    """Drop duplicate profiles from every run not in flight, and checkpoints from runs nothing can resume.

    A dry run reads and reports. Applying is safe beside a live application: each run is rewritten under its row lock
    from what the row holds at that moment, and is left alone if it became active. Rerunning finds nothing to do.
    """
    import time

    report = {"applied": apply, "runs_scanned": 0, "runs_changed": 0, "profiles_removed": 0, "checkpoints_removed": 0,
              "snapshots_added": 0, "raw_bytes_before": 0, "raw_bytes_after": 0, "stored_bytes_before": 0,
              "stored_bytes_after_estimate": 0, "skipped_active": 0, "skipped_recovery": 0}
    known, new_versions, last = set(), set(), ""
    is_postgres = db.engine.dialect.name == "postgresql"
    while True:
        with db.session() as s:
            ids = s.execute(
                select(Run.id, Run.state, Run.job_id).where(Run.id > last).order_by(Run.id).limit(batch)
            ).all()
            if not ids:
                break
            last = ids[-1][0]
            # A scheduled recovery resumes from a run in any state its row names. It is rare and never worth a guess.
            recovering = _recovering_jobs(s, {job for _, _, job in ids})
        for run_id, state, _ in ids:
            report["runs_scanned"] += 1
            if state in ACTIVE:
                report["skipped_active"] += 1
                continue
            with db.session() as s:
                query = select(Run).where(Run.id == run_id)
                row = s.scalar(query.with_for_update() if apply and is_postgres else query)
                if row is None or row.state in ACTIVE:
                    report["skipped_active"] += row is not None
                    continue
                keeps = row.job_id in recovering
                packet = row.packet or {}
                new, profile = slimmed(row.state, packet, keeps)
                if keeps and row.state in NEVER_RESUMED and "browser_checkpoint" in packet:
                    report["skipped_recovery"] += 1
                if new == packet:
                    continue
                raw_before, raw_after = len(json.dumps(packet)), len(json.dumps(new))
                stored = s.scalar(select(_column_size(db)).where(Run.id == run_id)) or raw_before
                report["raw_bytes_before"] += raw_before
                report["raw_bytes_after"] += raw_after
                report["stored_bytes_before"] += stored
                report["stored_bytes_after_estimate"] += round(stored * raw_after / max(raw_before, 1))
                report["runs_changed"] += 1
                report["profiles_removed"] += profile is not None
                report["checkpoints_removed"] += "browser_checkpoint" in packet and "browser_checkpoint" not in new
                if profile is not None:
                    version = new["profile_snapshot"]
                    if version not in known:
                        known.add(version)
                        if not _exists(s, version):
                            new_versions.add(version)
                            if apply:
                                # In this transaction: the packet gives up its copy only together with this row.
                                _store(s, version, profile)
                if apply:
                    row.packet = new
            if progress and report["runs_scanned"] % 500 == 0:
                progress(report)
        if stop and stop():
            report["stopped"] = True
            break
        time.sleep(pause)  # Beside live browsers: this is the background.
    report["snapshots_added"] = len(new_versions)
    report["stored_bytes_saved_estimate"] = report["stored_bytes_before"] - report["stored_bytes_after_estimate"]
    return report


def _column_size(db):
    from sqlalchemy import func

    return func.pg_column_size(Run.packet) if db.engine.dialect.name == "postgresql" else func.length(Run.packet)


def _recovering_jobs(session, job_ids):
    from .db import Setting

    keys = ["draft_recovery:" + job for job in job_ids]
    return {
        row.key.removeprefix("draft_recovery:")
        for row in session.scalars(select(Setting).where(Setting.key.in_(keys)))
        if (row.value or {}).get("scheduled")
    }
