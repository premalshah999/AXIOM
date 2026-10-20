"""Saving, listing and event delivery for applicant facts."""

import logging
import re

from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.orm import aliased

from .._store import new_id, norm, now
from . import employers
from .models import FactSaved, FactView, SaveAnswer, VersionConflict
from .questions import identify
from .tables import KnEvent, KnFact, KnVersion

log = logging.getLogger(__name__)
# Credentials, codes and identity numbers never enter the knowledge store.
SECRET = re.compile(
    r"password|passcode|passphrase|token|secret|\botp\b|one[- ]time code|verification code|api[_ -]?key|"
    r"credential|\bssn\b|social security|bank account|routing number|credit card",
    re.I,
)


def store_version(s, profile_id="default") -> int:
    row = s.get(KnVersion, profile_id)
    return row.version if row else 0


def bump(s, profile_id="default") -> int:
    row = s.get(KnVersion, profile_id)
    if row is None:
        row = KnVersion(profile_id=profile_id, version=0)
        s.add(row)
    row.version += 1
    row.updated_at = now()
    s.flush()
    return row.version


def current(q, at=None):
    """Live facts: not superseded, not retired."""
    return q.where(KnFact.superseded_by.is_(None), KnFact.retired.is_(False))


def not_shadowed():
    """A migrated mirror never speaks for a question the applicant answered natively.

    Native facts (saved through this service) own their (key, scope, employer).
    That includes superseded and retired ones: a retired native answer is a
    tombstone, so the legacy mirror of it cannot resurrect it after a profile sync.
    """
    native = aliased(KnFact)
    same_employer = or_(
        native.employer_id == KnFact.employer_id,
        and_(native.employer_id.is_(None), KnFact.employer_id.is_(None)),
    )
    owned = (
        exists()
        .where(
            native.profile_id == KnFact.profile_id,
            ~native.id.like("m:%"),
            native.key == KnFact.key,
            native.scope == KnFact.scope,
            same_employer,
        )
        .correlate(KnFact)
    )
    return ~and_(KnFact.id.like("m:%"), owned)


def readable(q):
    """What retrieval and the product show: live, and not shadowed by a native answer."""
    return current(q).where(not_shadowed())


def normalized(value):
    if isinstance(value, bool):
        return value
    text = norm(value)
    if text in {"yes", "true", "y"}:
        return True
    if text in {"no", "false", "n"}:
        return False
    return text


def view(f: KnFact) -> FactView:
    return FactView(
        id=f.id,
        category=f.category,
        key=f.key,
        question=f.question,
        value=(f.value or {}).get("v"),
        options=f.options or [],
        layer=f.layer,
        scope=f.scope,
        employer_id=f.employer_id,
        row_id=f.row_id,
        provenance=f.provenance or {},
        valid_from=f.valid_from,
        valid_until=f.valid_until,
        version=f.version,
    )


def save_confirmed_answer(db, data: SaveAnswer, profile_id="default", legacy=True) -> FactSaved:
    if SECRET.search(data.question):
        raise ValueError("Credentials, codes and identity numbers are not stored as knowledge")
    if data.value in (None, "", []):
        raise ValueError("Provide a value")
    with db.exclusive() as s:
        version = store_version(s, profile_id)
        if data.expected_version is not None and data.expected_version != version:
            raise VersionConflict(data.expected_version, version)
        employer = None
        if data.employer or data.employer_url:
            employer = employers.resolve(s, data.employer, data.employer_url)
        qid = identify(data.question, employer.aliases if employer else (), data.history_row)
        scope = data.scope
        # Relationship and motivation questions belong to one employer only.
        if qid.employer_specific:
            scope = "employer"
        if scope == "employer" and employer is None:
            raise ValueError("An employer-specific answer needs the employer")
        category = data.category or qid.category
        same = current(
            select(KnFact).where(
                KnFact.profile_id == profile_id,
                KnFact.key == qid.key,
                KnFact.scope == scope,
                KnFact.layer == data.layer,
            )
        )
        same = same.where(
            KnFact.employer_id == employer.id if scope == "employer" else KnFact.employer_id.is_(None)
        )
        old = list(s.scalars(same))
        fact = KnFact(
            id=new_id("f_"),
            profile_id=profile_id,
            category=category,
            key=qid.key,
            question=data.question.strip(),
            value={"v": data.value},
            options=list(data.options),
            layer=data.layer,
            scope=scope,
            employer_id=employer.id if scope == "employer" else None,
            row_id=data.history_row,
            provenance={
                "source": data.source,
                "actor": "applicant",
                "reference": str(data.correlation.get("review_id", "")),
                "at": now(),
                # The exact wording the applicant confirmed, for attestations.
                **(
                    {"statement": str(data.correlation["statement"])}
                    if data.correlation.get("statement")
                    else {}
                ),
            },
            valid_until=data.valid_until,
            version=max([f.version for f in old], default=0) + 1,
        )
        s.add(fact)
        s.flush()
        for f in old:
            f.superseded_by = fact.id
        new_version = bump(s, profile_id)
        event = KnEvent(
            id=new_id("ev_"),
            fact_id=fact.id,
            key=qid.key,
            employer_id=fact.employer_id,
            correlation={
                k: str(v) for k, v in data.correlation.items() if k in {"review_id", "run_id", "job_id"}
            },
        )
        s.add(event)
        result = FactSaved(
            fact_id=fact.id,
            version=fact.version,
            store_version=new_version,
            superseded=[f.id for f in old],
            event_id=event.id,
        )
        employer_name = employer.display if employer else ""
    # Compatibility: existing consumers read profile.reviewed_answers. The review
    # path already writes there itself, so only other sources are mirrored.
    if legacy and data.layer != "draft" and data.source != "review":
        try:
            from ...knowledge import save as legacy_save

            legacy_save(
                db,
                data.question,
                data.value if isinstance(data.value, str) else str(data.value),
                data.options,
                employer_name,
                "employer" if scope == "employer" else "personal",
            )
        except Exception:  # The new store is authoritative; never fail the save on the mirror.
            log.warning("Legacy knowledge mirror failed", exc_info=True)
    return result


def retire_fact(db, fact_id, expected_version=None, profile_id="default"):
    with db.exclusive() as s:
        version = store_version(s, profile_id)
        if expected_version is not None and expected_version != version:
            raise VersionConflict(expected_version, version)
        fact = s.get(KnFact, fact_id)
        if not fact:
            raise ValueError("Fact not found")
        fact.retired = True
        return bump(s, profile_id)


def list_facts(
    db,
    *,
    category=None,
    layer=None,
    employer_id=None,
    scope=None,
    q="",
    page=1,
    size=25,
    profile_id="default",
):
    size = max(1, min(100, size))
    page = max(1, page)
    with db.session() as s:
        query = readable(select(KnFact).where(KnFact.profile_id == profile_id))
        if category:
            query = query.where(KnFact.category == category)
        if layer:
            query = query.where(KnFact.layer == layer)
        if scope:
            query = query.where(KnFact.scope == scope)
        if employer_id:
            query = query.where(KnFact.employer_id == employer_id)
        if q:
            like = f"%{q.strip()[:120]}%"
            query = query.where(or_(KnFact.question.ilike(like), KnFact.key.ilike(like)))
        total = s.scalar(select(func.count()).select_from(query.subquery()))
        rows = s.scalars(
            query.order_by(KnFact.category, KnFact.created_at.desc()).offset((page - 1) * size).limit(size)
        )
        return {
            "items": [view(f).model_dump() for f in rows],
            "total": total,
            "page": page,
            "size": size,
            "store_version": store_version(s, profile_id),
        }


def pending_events(db, limit=50):
    """For the executor: fact saves that may resume a waiting application."""
    with db.session() as s:
        rows = s.scalars(
            select(KnEvent).where(KnEvent.state == "pending").order_by(KnEvent.created_at).limit(limit)
        )
        return [
            {
                "id": e.id,
                "kind": e.kind,
                "fact_id": e.fact_id,
                "key": e.key,
                "employer_id": e.employer_id,
                "correlation": e.correlation,
                "created_at": e.created_at,
            }
            for e in rows
        ]


def ack_event(db, event_id):
    with db.exclusive() as s:
        row = s.get(KnEvent, event_id)
        if row and row.state == "pending":
            row.state, row.delivered_at = "delivered", now()
            return True
        return False
