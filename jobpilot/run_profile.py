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

from .db import ProfileSnapshot, now
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
