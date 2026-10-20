"""Evidence packets for one form question.

Order: exact confirmed facts (indexed by meaning-preserving key) → history rows
→ resume-variant rows → structured facts of the question's category → native
confirmed answers of that category (paraphrase candidates, never exact) → a few
lexically ranked supported narratives. No model and no vector index is needed
for an exact confirmed fact. Employer-scoped memory is visible only for the
matching canonical employer.
"""

import json
from collections import OrderedDict
from datetime import UTC, datetime

from sqlalchemy import or_, select, text

from .._store import digest, norm, words
from . import employers
from .migration import sync
from .models import Budget, EvidenceItem, EvidencePacket
from .questions import identify, structured_key
from .store import normalized, readable, store_version
from .tables import KnFact

# Categories where an absent fact must surface as missing, never be inferred.
DISCLOSURES = {"work_authorization", "demographics", "clearance", "agreements", "employer_relationship"}
ROW_CATEGORIES = {"education": "kf:education_row", "experience": "kf:work_row"}
NARRATIVE_CATEGORIES = {"experience", "projects", "skills", "additional", "employer_relationship"}
# Plain field labels that name one structured profile fact exactly.
LABEL_FIELDS = {
    "first name": "first_name",
    "legal first name": "first_name",
    "given name": "first_name",
    "last name": "last_name",
    "legal last name": "last_name",
    "family name": "last_name",
    "surname": "last_name",
    "middle name": "middle_name",
    "preferred name": "preferred_name",
    "full name": "name",
    "name": "name",
    "email": "email",
    "email address": "email",
    "e-mail": "email",
    "phone": "phone",
    "phone number": "phone",
    "mobile phone": "phone",
    "linkedin": "linkedin",
    "linkedin profile": "linkedin",
    "linkedin url": "linkedin",
    "github": "github",
    "github url": "github",
    "website": "website",
    "portfolio": "website",
    "city": "address_city",
    "state": "address_state",
    "zip code": "address_postal_code",
    "postal code": "address_postal_code",
    "country": "address_country",
    "address": "address_line1",
    "address line 1": "address_line1",
    "street address": "address_line1",
}
_CACHE: "OrderedDict[str, EvidencePacket]" = OrderedDict()
_CACHE_SIZE = 512


def _instant(value):
    try:
        t = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return t if t.tzinfo else t.replace(tzinfo=UTC)


def _expired(f: KnFact, at: str) -> bool:
    """Not current: past its validity window, not yet valid, or with an unreadable window."""
    now_ = _instant(at)
    if f.valid_until:
        until = _instant(f.valid_until)
        if until is None or until <= now_:
            return True
    if f.valid_from:
        start = _instant(f.valid_from)
        if start is None or start > now_:
            return True
    return False


def _item(f: KnFact, kind, match) -> EvidenceItem:
    return EvidenceItem(
        fact_id=f.id,
        kind=kind,
        match=match,
        category=f.category,
        question=f.question,
        value=(f.value or {}).get("v"),
        scope=f.scope,
        employer_id=f.employer_id,
        row_id=f.row_id,
        version=f.version,
        source=(f.provenance or {}).get("source", ""),
    )


def option_for(value, options):
    """Exact option compatible with a confirmed value, or None."""
    if not options:
        return None
    want = normalized(value)
    for option in options:
        if normalized(option) == want:
            return option
    if isinstance(want, bool):
        prefix = "yes" if want else "no"
        hits = [o for o in options if norm(o) == prefix or norm(o).startswith(prefix + ",")]
        if len(hits) == 1:
            return hits[0]
    return None


def comparable(value):
    """A confirmed value for conflict detection: spelling variants of one country, or that
    country with a phone calling code saved from a phone picker, are the same answer.

    Westinghouse/Oracle "Country": "United States", "United States of America" and
    "United States +1" were treated as conflicting, turning a known fact into a question.
    Only whole-country alias groups are used; distinct countries still conflict.
    """
    import re

    from ...widgets import ALIASES

    v = normalized(value)
    if isinstance(v, str):
        bare = re.sub(r"\s*\(?\+\s*\d{1,4}\)?\s*$", "", v).strip()
        for group in ALIASES:
            if {"united states", "united kingdom"} & group and bare in group:
                return "country:" + sorted(group)[0]
    return v


def _overlap(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


_PROFILE_DIGESTS: dict = {}


def current_profile_digest(db):
    """Hash the ~100 KB profile only when its row changed.

    Every form question used to reload and hash it, which on 300-field forms
    kept the shared event loop busy. PostgreSQL's row xmin changes on every
    update, so it is an exact, cheap change marker; other databases re-hash.
    """
    if db.engine.dialect.name != "postgresql":
        return digest(db.get_setting("profile", {}) or {})
    with db.session() as s:
        marker = s.execute(text("select xmin::text from settings where key = 'profile'")).scalar()
    cached = _PROFILE_DIGESTS.get(id(db))
    if marker and cached and cached[0] == marker:
        return cached[1]
    value = digest(db.get_setting("profile", {}) or {})
    if marker:
        _PROFILE_DIGESTS[id(db)] = (marker, value)
    return value


def retrieve_answer_context(
    db,
    question: str,
    options=(),
    employer: str = "",
    employer_url: str = "",
    history_row: str | None = None,
    resume_artifact_id: str | None = None,
    budget: Budget | None = None,
    profile_id: str = "default",
    auto_sync: bool = True,
) -> EvidencePacket:
    budget = budget or Budget()
    full_digest = current_profile_digest(db)
    if auto_sync:
        sync(db, profile_id, full_digest)
    profile_digest = full_digest[:16]
    at = datetime.now(UTC).isoformat()
    with db.session() as s:
        version = store_version(s, profile_id)
        ident = (
            employers.resolve(s, employer, employer_url, create=False) if (employer or employer_url) else None
        )
        aliases = tuple(
            employers.mention_aliases([employer, *((ident.display, *ident.aliases) if ident else ())])
        )
        qid = identify(question, aliases, history_row)
        cache_key = digest(
            [
                version,
                profile_digest,
                qid.key,
                ident.id if ident else "",
                list(options),
                resume_artifact_id,
                budget.model_dump(),
                at[:10],
            ]
        )
        hit = _CACHE.get(cache_key)
        if hit is not None:
            _CACHE.move_to_end(cache_key)
            return hit.model_copy(update={"cache_hit": True})

        visible = or_(
            KnFact.scope == "global",
            (KnFact.scope == "employer") & (KnFact.employer_id == (ident.id if ident else "__none__")),
        )
        base = readable(
            select(KnFact).where(KnFact.profile_id == profile_id, KnFact.layer != "draft", visible)
        )
        expired: list[str] = []

        # 1. Exact meaning-preserving key.
        keys = [qid.key]
        if qid.text in LABEL_FIELDS:
            keys.append(structured_key(LABEL_FIELDS[qid.text]))
        exact = []
        for f in s.scalars(base.where(KnFact.key.in_(keys), KnFact.layer == "confirmed")):
            if history_row is not None and f.row_id not in (None, history_row):
                continue
            (expired.append(f.id) if _expired(f, at) else exact.append(f))
        # An employer-specific answer outranks a global one for that employer.
        tier = [f for f in exact if f.scope == "employer"] or [f for f in exact if f.scope == "global"]
        values = {json.dumps(comparable((f.value or {}).get("v")), default=str) for f in tier}
        conflicts = [[f.id for f in tier]] if len(values) > 1 else []
        exact_answer = exact_option = None
        if tier and not conflicts:
            # Prefer a value without a phone calling code ("United States +1" came from a picker).
            plain = [f for f in tier if "+" not in str((f.value or {}).get("v"))]
            value = ((plain or tier)[-1].value or {}).get("v")
            if options:
                exact_option = option_for(value, options)
                exact_answer = value if exact_option else None
            else:
                exact_answer = value

        items = [_item(f, "fact", "exact") for f in exact]
        # 2. History rows for education/experience questions (row identity kept).
        row_key = ROW_CATEGORIES.get(qid.category)
        if row_key:
            rows = [f for f in s.scalars(base.where(KnFact.key == row_key)) if not _expired(f, at)]
            if history_row:
                rows.sort(key=lambda f: f.row_id != history_row)
            items += [_item(f, "history_row", "row") for f in rows]
        # 3. The exact rows and wording of the resume variant being submitted.
        if resume_artifact_id:
            try:
                from ..resumes import variant_rows

                for row in variant_rows(db, resume_artifact_id):
                    items.append(
                        EvidenceItem(
                            fact_id=row["unit_id"],
                            kind="variant_row",
                            match="variant",
                            category=row.get("kind", "experience"),
                            question="",
                            value=row["text"],
                            scope="global",
                            row_id=row.get("source_row"),
                            source="resume_variant:" + resume_artifact_id,
                        )
                    )
            except Exception:
                pass
        # 4. Structured facts of the same category (e.g. authorization facts).
        seen = {i.fact_id for i in items}
        category_facts = [
            f
            for f in s.scalars(
                base.where(
                    KnFact.category == qid.category, KnFact.layer == "confirmed", KnFact.key.like("kf:%")
                )
            )
            if f.id not in seen and f.key not in ROW_CATEGORIES.values()
        ]
        for f in category_facts:
            (expired.append(f.id) if _expired(f, at) else items.append(_item(f, "fact", "category")))
        # 4b. Native confirmed answers of the same category, keyed by their own question.
        # A paraphrase changes the exact key, so these surface as candidates the
        # reasoner must interpret; they never become exact_answer. Migration mirrors
        # (m:) are excluded: their native source or structured fact is used instead.
        seen = {i.fact_id for i in items}
        candidates = s.scalars(
            base.where(
                KnFact.category == qid.category,
                KnFact.layer == "confirmed",
                ~KnFact.key.like("kf:%"),
                ~KnFact.id.like("m:%"),
            )
            .order_by(KnFact.created_at.desc())
            .limit(budget.candidates)
        )
        for f in candidates:
            if f.id in seen or (f.row_id and f.row_id != history_row):
                continue
            (expired.append(f.id) if _expired(f, at) else items.append(_item(f, "fact", "category")))
        # 5. Supported narratives, only for questions that ask for one.
        narrative_q = (
            qid.employer_specific or qid.category in NARRATIVE_CATEGORIES or qid.modality == "experience"
        )
        if narrative_q and budget.narratives:
            mine = set(words(qid.text))
            scored = []
            for f in s.scalars(base.where(KnFact.layer == "narrative")):
                text = f"{f.question} {(f.value or {}).get('v', '')}"
                score = _overlap(mine, set(words(text))) + (0.2 if f.scope == "employer" else 0)
                if score >= 0.05:
                    scored.append((score, f))
            scored.sort(key=lambda x: -x[0])
            items += [_item(f, "narrative", "lexical") for _, f in scored[: budget.narratives]]

    missing = []
    if qid.category in DISCLOSURES and not exact and not category_facts:
        missing.append(qid.category)
    # Budget: highest-precision items first; stop at entry/byte caps.
    kept, used, truncated = [], 0, False
    for item in items:
        size = len(json.dumps(item.model_dump(), default=str))
        if len(kept) >= budget.max_items or used + size > budget.max_bytes:
            truncated = True
            continue
        kept.append(item)
        used += size
    packet = EvidencePacket(
        question=qid,
        employer=ident,
        store_version=version,
        profile_digest=profile_digest,
        exact_answer=exact_answer,
        exact_option=exact_option,
        items=kept,
        conflicts=conflicts,
        expired=expired,
        missing=missing,
        truncated=truncated,
        bytes=used,
    )
    _CACHE[cache_key] = packet
    if len(_CACHE) > _CACHE_SIZE:
        _CACHE.popitem(last=False)
    return packet


def clear_cache():
    _CACHE.clear()
