"""Profile → structured facts. Idempotent, incremental and rollbackable.

The legacy `profile` setting is read, never written, so existing consumers keep
working and rollback is a delete of migrated rows only. Migrated rows have
deterministic IDs (`m:<source path>`); re-running with an unchanged profile is
a no-op, and a changed profile updates/retires only migrated rows. Facts saved
through the new API (non-`m:` IDs) are never touched by migration.
"""

import re
import time
from weakref import WeakKeyDictionary

from sqlalchemy import select

from .._store import digest, new_id, norm, now
from . import employers
from .questions import classify, identify, structured_key
from .store import SECRET, bump
from .tables import KnFact, KnMigration

SOURCE = "profile_migration"
PERSONAL = (
    "name",
    "first_name",
    "middle_name",
    "last_name",
    "preferred_name",
    "email",
    "phone",
    "phone_country_code",
    "phone_type",
    "location",
    "linkedin",
    "github",
    "website",
)
ADDITIONAL = ("salary_expectation", "availability", "application_source")
FACT_CATEGORY = {
    "personal": "personal",
    "work_authorization": "work_authorization",
    "demographics": "demographics",
    "clearance": "clearance",
    "agreements": "agreements",
    "education": "education",
    "experience": "experience",
    "skills": "skills",
    "employer_relationship": "employer_relationship",
}


def row_id(prefix, row, keys):
    return prefix + ":" + digest([norm(row.get(k, "")) for k in keys])[:12]


def desired_facts(s, profile: dict) -> tuple[list[dict], dict]:
    """Every fact the profile implies, with a deterministic ID."""
    out, skipped = [], {"secret_keys": 0, "empty": 0}

    def add(
        fid,
        category,
        key,
        value,
        *,
        question="",
        layer="confirmed",
        scope="global",
        employer_id=None,
        row=None,
        options=None,
        reference="",
    ):
        if value in (None, "", [], {}):
            skipped["empty"] += 1
            return
        out.append(
            {
                "id": "m:" + fid,
                "category": category,
                "key": key,
                "question": question,
                "value": {"v": value},
                "options": list(options or []),
                "layer": layer,
                "scope": scope,
                "employer_id": employer_id,
                "row_id": row,
                "provenance": {"source": SOURCE, "actor": "applicant", "reference": reference},
            }
        )

    for key in PERSONAL:
        add(f"personal:{key}", "personal", structured_key(key), profile.get(key))
    for key, value in (profile.get("address") or {}).items():
        add(f"address:{key}", "personal", structured_key("address_" + key), value)
    for key in ADDITIONAL:
        add(f"additional:{key}", "additional", structured_key(key), profile.get(key))
    # History rows keep a content-derived identity, so both degrees and every role survive.
    for i, row in enumerate(profile.get("education") or []):
        rid = row_id("edu", row, ("school", "degree", "field_of_study", "start"))
        add(
            f"education:{rid}",
            "education",
            "kf:education_row",
            dict(row),
            row=rid,
            reference=f"education[{i}]",
        )
    for i, row in enumerate(profile.get("work") or []):
        rid = row_id("exp", row, ("company", "title", "start"))
        add(f"experience:{rid}", "experience", "kf:work_row", dict(row), row=rid, reference=f"work[{i}]")
    add("skills", "skills", "kf:skills", list(profile.get("skills") or []))
    add("languages", "skills", "kf:languages", list(profile.get("languages") or []))
    for key, value in (profile.get("facts") or {}).items():
        if SECRET.search(key):
            skipped["secret_keys"] += 1
            continue
        category = FACT_CATEGORY.get(classify(norm(key.replace("_", " "))), "additional")
        add(f"fact:{key}", category, structured_key(key), value, reference=f"facts.{key}")
    # Narrative evidence keeps its original evidence ID as row and reference.
    for note in profile.get("evidence") or []:
        text = (note or {}).get("text", "")
        nid = (note or {}).get("id") or digest(text)[:10]
        category = "projects" if re.match(r"\s*project\b", text, re.I) else "experience"
        add(
            f"evidence:{nid}", category, f"kn:evidence:{nid}", text, layer="narrative", row=nid, reference=nid
        )
    forgotten = set(profile.get("forgotten_answers") or [])
    for answer in profile.get("reviewed_answers") or []:
        if answer.get("id") in forgotten or SECRET.search(answer.get("question", "")):
            continue
        employer = None
        scoped = answer.get("scope") == "employer" and answer.get("employer")
        if scoped:
            employer = employers.resolve(s, answer["employer"])
        qid = identify(answer.get("question", ""), employer.aliases if employer else ())
        add(
            f"review:{answer.get('id')}",
            qid.category,
            qid.key,
            answer.get("answer"),
            question=answer.get("question", ""),
            layer="narrative" if answer.get("layer") == "narrative" else "confirmed",
            scope="employer" if employer else "global",
            employer_id=employer.id if employer else None,
            options=answer.get("options"),
            reference=answer.get("id", ""),
        )
    for review_key, value in (profile.get("approved_answers") or {}).items():
        add(f"approved:{review_key}", "additional", "review:" + review_key, value, reference=review_key)
    return out, skipped


KNOWN = {
    *PERSONAL,
    *ADDITIONAL,
    "address",
    "education",
    "work",
    "skills",
    "languages",
    "facts",
    "evidence",
    "reviewed_answers",
    "approved_answers",
    "forgotten_answers",
}


def migrate_profile(db, profile_id="default", dry_run=False) -> dict:
    profile = db.get_setting("profile", {}) or {}
    source_digest = digest(profile)
    with db.exclusive() as s:
        last = s.scalar(
            select(KnMigration)
            .where(KnMigration.profile_id == profile_id, KnMigration.rolled_back.is_(False))
            .order_by(KnMigration.created_at.desc())
        )
        if last and last.source_digest == source_digest:
            return {"status": "unchanged", "migration_id": last.id, "counts": last.counts}
        desired, skipped = desired_facts(s, profile)
        existing = {
            f.id: f
            for f in s.scalars(select(KnFact).where(KnFact.profile_id == profile_id, KnFact.id.like("m:%")))
        }
        counts = {"created": 0, "updated": 0, "retired": 0, "unchanged": 0, **skipped}
        wanted = {d["id"] for d in desired}
        for d in desired:
            row = existing.get(d["id"])
            if row is None:
                counts["created"] += 1
                if not dry_run:
                    s.add(KnFact(profile_id=profile_id, provenance={**d.pop("provenance"), "at": now()}, **d))
                continue
            changed = (
                row.value != d["value"]
                or row.question != d["question"]
                or row.retired
                or row.employer_id != d["employer_id"]
            )
            if not changed:
                counts["unchanged"] += 1
                continue
            counts["updated"] += 1
            if not dry_run:
                history = (row.provenance or {}).get("history", [])[-4:] + [
                    {"value": row.value, "version": row.version}
                ]
                for k in (
                    "category",
                    "key",
                    "question",
                    "value",
                    "options",
                    "layer",
                    "scope",
                    "employer_id",
                    "row_id",
                ):
                    setattr(row, k, d[k])
                row.retired = False
                row.version += 1
                row.provenance = {**d["provenance"], "at": now(), "history": history}
        for fid, row in existing.items():
            if fid not in wanted and not row.retired:
                counts["retired"] += 1
                if not dry_run:
                    row.retired = True
        extras = sorted(k for k in profile if k not in KNOWN)
        report = {
            "status": "dry_run" if dry_run else "migrated",
            "counts": counts,
            "facts": len(desired),
            # Keys this migration does not structure stay in the profile untouched.
            "unstructured_profile_keys": extras,
        }
        if dry_run:
            return report
        migration = KnMigration(
            id=new_id("mig_"),
            profile_id=profile_id,
            source_digest=source_digest,
            fact_ids=sorted(wanted),
            counts=counts,
        )
        s.add(migration)
        bump(s, profile_id)
        return {**report, "migration_id": migration.id}


def rollback(db, profile_id="default") -> dict:
    """Remove every migrated row. The profile setting was never modified, and
    facts saved through the new API are kept."""
    _SYNCED.get(db, {}).pop(profile_id, None)
    with db.exclusive() as s:
        rows = list(s.scalars(select(KnFact).where(KnFact.profile_id == profile_id, KnFact.id.like("m:%"))))
        for row in rows:
            s.delete(row)
        for m in s.scalars(select(KnMigration).where(KnMigration.profile_id == profile_id)):
            m.rolled_back = True
        bump(s, profile_id)
        return {"deleted": len(rows)}


CATEGORY_SETTING = "knowledge:category_rules"


def recategorize(db, profile_id="default") -> int:
    """Apply newer category rules to saved facts once; values and keys are untouched.

    The question key never depends on the category, so exact lookups are unaffected.
    Employer-relationship categories can depend on the employer's aliases at save
    time, so they are kept; structured kf: facts keep their mapped category.
    """
    from .questions import CATEGORY_VERSION

    if db.get_setting(CATEGORY_SETTING, "") == CATEGORY_VERSION:
        return 0
    moved = 0
    with db.exclusive() as s:
        rows = s.scalars(
            select(KnFact).where(
                KnFact.profile_id == profile_id,
                ~KnFact.key.like("kf:%"),
                KnFact.category != "employer_relationship",
            )
        )
        for f in rows:
            category = identify(f.question or "").category
            if category != f.category:
                f.category = category
                moved += 1
        if moved:
            bump(s, profile_id)
    db.set_setting(CATEGORY_SETTING, CATEGORY_VERSION)
    return moved


# {database: {profile id: (the digest found current, when)}}. Every form question asked again: about 10 reads a second of
# kn_migrations (all 252 rows each) on the loop the browsers share (2026-10-30), for an answer that changes when the
# profile does. A profile edit gives a new digest, which misses here at once; the recheck covers a rollback elsewhere.
_SYNCED = WeakKeyDictionary()
SYNC_RECHECK_SECONDS = 60.0


def sync(db, profile_id="default", source_digest=None):
    """Cheap lock-free check before retrieval; migrates only when the profile changed."""
    kept = _SYNCED.setdefault(db, {})
    current = kept.get(profile_id)
    if (
        source_digest
        and current
        and current[0] == source_digest
        and time.monotonic() - current[1] < SYNC_RECHECK_SECONDS
    ):
        return {"status": "unchanged"}
    recategorize(db, profile_id)
    source_digest = source_digest or digest(db.get_setting("profile", {}) or {})
    with db.session() as s:
        last = s.scalar(
            select(KnMigration.source_digest)
            .where(KnMigration.profile_id == profile_id, KnMigration.rolled_back.is_(False))
            .order_by(KnMigration.created_at.desc())
            .limit(1)
        )
    if last == source_digest:
        kept[profile_id] = (source_digest, time.monotonic())
        return {"status": "unchanged"}
    kept.pop(profile_id, None)
    return migrate_profile(db, profile_id)
