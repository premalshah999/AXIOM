"""Execution-owned integration with feature services.

Feature packages choose evidence and produce artifacts. This boundary validates
their bindings before queueing, pins immutable run context, and delivers fact
events through the existing durable application outbox.
"""

import asyncio
import copy
import hashlib
import json
from datetime import UTC, datetime

from sqlalchemy import or_, select

from .admission import QueueBlocked


def enabled(db):
    return bool(db.get_setting("execution_services", {}).get("enabled"))


def master_policy(db):
    policy = db.get_setting("application_resume_policy", {})
    return policy if policy.get("mode") == "master" else None


def ready_for_job(db, job_id, data_dir=None):
    """Only a variant matching the current source, profile and posting is ready."""
    from .features.resumes import current_artifact

    return current_artifact(db, job_id, data_dir)


def packet_metadata(packet):
    """Scope/reference metadata; values already live in the model evidence map."""
    return {
        "question": packet.question.model_dump(),
        "employer_id": packet.employer.id if packet.employer else None,
        "store_version": packet.store_version,
        "references": [
            {
                "id": "knowledge:" + item.fact_id,
                "kind": item.kind,
                "match": item.match,
                "scope": item.scope,
                "row_id": item.row_id,
                "version": item.version,
            }
            for item in packet.items
        ],
        "conflicts": packet.conflicts,
        "missing": packet.missing,
        "expired": packet.expired,
    }


def valid_now(fact):
    at = datetime.now(UTC)
    for key, lower in (("valid_from", True), ("valid_until", False)):
        value = getattr(fact, key, None)
        if not value:
            continue
        try:
            instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
            instant = instant if instant.tzinfo else instant.replace(tzinfo=UTC)
        except (ValueError, TypeError):
            return False
        if (lower and instant > at) or (not lower and instant <= at):
            return False
    return True


def confirmed_candidates(db, packet):
    """Expose confirmed free-form answers to reasoning across paraphrases.

    Feature retrieval currently includes category candidates only for `kf:`
    structured keys. Native saved answers have question keys, so a small wording
    change otherwise makes them disappear. Candidates never become exact answers.
    """
    from .features.knowledge.models import EvidenceItem
    from .features.knowledge.store import readable
    from .features.knowledge.tables import KnFact

    seen = {item.fact_id for item in packet.items}
    items = list(packet.items)
    used = sum(len(json.dumps(item.model_dump(), default=str).encode()) for item in items)
    with db.session() as session:
        rows = session.scalars(
            readable(select(KnFact))
            .where(
                KnFact.profile_id == "default",
                KnFact.layer == "confirmed",
                KnFact.category == packet.question.category,
                ~KnFact.id.like("m:%"),
                or_(
                    KnFact.scope == "global",
                    (KnFact.scope == "employer")
                    & (KnFact.employer_id == (packet.employer.id if packet.employer else "__none__")),
                ),
            )
            .order_by(KnFact.created_at.desc())
            .limit(20)
        )
        for row in rows:
            if row.id in seen or not valid_now(row):
                continue
            if row.row_id and row.row_id != packet.question.history_row:
                continue
            item = EvidenceItem(
                fact_id=row.id,
                kind="fact",
                match="category",
                category=row.category,
                question=row.question,
                value=(row.value or {}).get("v"),
                scope=row.scope,
                employer_id=row.employer_id,
                row_id=row.row_id,
                version=row.version,
                source=(row.provenance or {}).get("source", ""),
            )
            size = len(json.dumps(item.model_dump(), default=str).encode())
            if len(items) >= 10 or used + size > 4000:
                continue
            items.append(item)
            used += size
    return packet.model_copy(update={"items": items, "bytes": used})


def authoritative_packet(db, packet, options):
    """Native user edits outrank their legacy migration mirrors.

    Compatibility mirroring must not resurrect retired facts or introduce a
    conflict between an edited fact and its old migrated copy. This consumer
    guard can be removed when the feature retrieval API enforces that rule.
    Returns a packet and an optional blocker for local-answer fallbacks.
    """
    from .features.knowledge.models import EvidenceItem
    from .features.knowledge.retrieval import comparable, option_for
    from .features.knowledge.tables import KnFact

    packet = confirmed_candidates(db, packet)

    with db.session() as session:
        rows = list(
            session.scalars(
                select(KnFact)
                .where(
                    KnFact.profile_id == "default",
                    KnFact.key == packet.question.key,
                    KnFact.layer == "confirmed",
                    or_(KnFact.row_id.is_(None), KnFact.row_id == packet.question.history_row)
                    if packet.question.history_row
                    else KnFact.row_id.is_(None),
                    ~KnFact.id.like("m:%"),
                    or_(
                        KnFact.scope == "global",
                        (KnFact.scope == "employer")
                        & (KnFact.employer_id == (packet.employer.id if packet.employer else "__none__")),
                    ),
                )
                .order_by(KnFact.created_at.desc())
            )
        )
        tier = [r for r in rows if r.scope == "employer"] or rows
        if not tier:
            return packet, None
        current = [r for r in tier if not r.superseded_by and not r.retired]
        if not current:
            return packet.model_copy(
                update={"exact_answer": None, "exact_option": None, "items": []}
            ), "Confirmed answer was retired"
        valid = [r for r in current if valid_now(r)]
        if not valid:
            return packet.model_copy(
                update={"exact_answer": None, "exact_option": None, "items": []}
            ), "Confirmed answer is outside its validity period"
        values = {
            json.dumps(comparable((r.value or {}).get("v")), sort_keys=True, default=str) for r in valid
        }
        if len(values) != 1:
            # The existing interpreter must see all current native facts before
            # deciding whether different wording is a real disagreement.
            exact = [
                EvidenceItem(
                    fact_id=r.id,
                    kind="fact",
                    match="exact",
                    category=r.category,
                    question=r.question,
                    value=(r.value or {}).get("v"),
                    scope=r.scope,
                    employer_id=r.employer_id,
                    row_id=r.row_id,
                    version=r.version,
                    source=(r.provenance or {}).get("source", ""),
                )
                for r in valid
            ]
            if len(json.dumps([i.model_dump() for i in exact], default=str).encode()) > 4000:
                return packet, "Confirmed conflict exceeds the retrieval budget"
            return packet.model_copy(
                update={
                    "exact_answer": None,
                    "exact_option": None,
                    "items": exact,
                    "conflicts": [[r.id for r in valid]],
                }
            ), None
        row = valid[0]
        value = (row.value or {}).get("v")
        option = option_for(value, options) if options else None
        items = [
            EvidenceItem(
                fact_id=row.id,
                kind="fact",
                match="exact",
                category=row.category,
                question=row.question,
                value=value,
                scope=row.scope,
                employer_id=row.employer_id,
                row_id=row.row_id,
                version=row.version,
                source=(row.provenance or {}).get("source", ""),
            )
        ]
        items += [i for i in packet.items if i.match != "exact"]
        kept, size = [], 0
        for item in items:
            count = len(json.dumps(item.model_dump(), default=str).encode())
            if len(kept) < 10 and size + count <= 4000:
                kept.append(item)
                size += count
        if not kept or kept[0].fact_id != row.id:
            return packet.model_copy(
                update={"exact_answer": None, "exact_option": None, "items": []}
            ), "Confirmed answer exceeds the retrieval budget"
        return packet.model_copy(
            update={
                "exact_answer": value if not options or option else None,
                "exact_option": option,
                "conflicts": [],
                "items": kept,
                "bytes": size,
                "truncated": packet.truncated or len(kept) < len(items),
            }
        ), None


async def prepare_due(service):
    """Request bounded durable tailoring without holding browser slots."""
    from .features.discovery import discover_due, policy_for
    from .workers import enqueue_tailoring

    policy = policy_for(service.db).model_copy(update={"owners": service.config.workers})
    due = await asyncio.to_thread(discover_due, service.db, policy)
    if master_policy(service.db):
        return due  # Retrieval/discovery stay enabled; no automatic PDF preparation.
    from .db import Job, Run, Setting
    from .features.discovery import gate

    with service.db.session() as session:
        candidates = [
            {"id": j.id, "title": j.title, "url": j.url, "location": j.location, "description": j.description}
            for j in session.scalars(
                select(Job)
                .where(
                    Job.status == "ready",
                    Job.demo.is_(False),
                    Job.id.not_in(select(Run.job_id)),
                    Job.score >= policy.minimum_fit,
                )
                .limit(policy.owners * policy.tailor_lookahead)
            )
        ]
        controlled = {
            r.key.removeprefix("job_control:")
            for r in session.scalars(select(Setting).where(Setting.key.like("job_control:%")))
            if r.value.get("state") in {"paused", "deleted"}
        }
    stale = [
        j["id"]
        for j in candidates
        if j["id"] not in controlled
        and not gate(j, policy)
        and ready_for_job(service.db, j["id"], service.config.data_dir) is None
    ]
    from .reserved import held_ids

    # No tailored resume (model calls) for a reserved employer's job before the owner approves it (2026-10-27); an
    # approved Apply asks for one itself (tailoring_pending).
    waiting = await asyncio.to_thread(held_ids, service.db)
    due_ids = [job_id for job_id in dict.fromkeys([*due.tailor, *stale]) if job_id not in waiting]
    for job_id in due_ids[: policy.owners * policy.tailor_lookahead]:
        await enqueue_tailoring(job_id)
    return due


async def finish_preparation(service, job_id, artifact_id):
    """Resume an explicit queue request; all queue guards run again."""
    key = "preparation_request:" + job_id
    request = service.db.get_setting(key, {})
    if request.get("state") != "pending":
        return None
    args = {k: request.get(k) for k in ("mode", "resume_id", "retry", "batch_id", "controller_policy")}
    try:
        run = await service.queue(job_id, resume_artifact_id=artifact_id, **args)
    except QueueBlocked as exc:
        if exc.outcome != "protected":
            return None
        result = {"state": "protected", "code": exc.code}
    except ValueError:
        result = {"state": "blocked"}
    else:
        result = {"state": "queued", "run_id": run["id"]}
    from .db import Setting

    with service.db.exclusive() as session:
        current = session.get(Setting, key)
        if current and current.value.get("request_id") == request["request_id"]:
            current.value = {**current.value, **result}
    return result


def pin_resume(session, config, job, source, profile, artifact_id=None, continuation=None):
    """Bind a ready artifact to this job; a continuation cannot change its PDF."""
    from .features._store import digest
    from .features.resumes.evidence import profile_digest
    from .features.resumes.pipeline import job_identity
    from .features.resumes.tables import RsArtifact

    previous = continuation.packet if continuation else {}
    pinned = previous.get("resume_artifact_id")
    if pinned and artifact_id and pinned != artifact_id:
        raise QueueBlocked(
            "A resumed application cannot switch resume variants", code="resume_variant_changed"
        )
    artifact_id = pinned or artifact_id
    if not artifact_id:
        return {}, profile
    row = session.get(RsArtifact, artifact_id)
    if (
        not row
        or row.status != "ready"
        or row.job_identity != job_identity(job.url)
        or row.source_resume_id != source.id
        or row.source_resume_sha != source.sha256
        or not row.pdf_path
        or not row.pdf_sha
    ):
        raise QueueBlocked(
            "Resume variant is not ready for this job and source resume", code="invalid_resume_variant"
        )
    if not pinned and (
        row.profile_digest != profile_digest(profile)
        or row.description_sha != digest(job.description or "")[:20]
    ):
        raise QueueBlocked("Resume variant is stale; prepare it again", code="stale_resume_variant")
    root = config.data_dir.resolve()
    path = (root / row.pdf_path).resolve()
    if not path.is_relative_to(root / "tailored") or not path.is_file():
        raise QueueBlocked("Resume artifact file is unavailable", code="resume_artifact_missing")
    if hashlib.sha256(path.read_bytes()).hexdigest() != row.pdf_sha:
        raise QueueBlocked("Resume artifact integrity check failed", code="resume_artifact_changed")
    # Retrying a question refreshes disclosures/contact facts while retaining
    # the exact employment/education context already attached to this draft.
    bound = copy.deepcopy(profile)
    if pinned:
        from .run_profile import full as full_profile

        started_with = full_profile(previous, session)
        for key in ("work", "education", "evidence", "skills", "languages"):
            bound[key] = copy.deepcopy(started_with.get(key, bound.get(key)))
    else:
        from .features.knowledge.migration import row_id

        selected = {}
        for item in row.selection or []:
            if item.get("kind") == "experience":
                selected.setdefault(item.get("source_row"), []).append(item.get("text", ""))
        for work in bound.get("work", []):
            ident = row_id("exp", work, ("company", "title", "start"))
            if ident in selected:
                work["description"] = "\n".join(selected[ident])
    return {
        "resume_artifact_id": artifact_id,
        "resume_path": str(path),
        "resume_sha": row.pdf_sha,
        "resume_text": row.text,
        "resume_variant": {
            "profile_digest": row.profile_digest,
            "description_sha": row.description_sha,
            "policy_version": row.policy_version,
            "selection": copy.deepcopy(row.selection or []),
        },
    }, bound


def forward_fact_events(service):
    """Commit correlated resume delivery before acknowledging a feature event.

    An outbox key derived from the event ID makes replay after a crash harmless.
    Correlation is validated against actual review/run/job ownership.
    """
    from .db import Job, Review, Run
    from .features.knowledge import ack_event, pending_events
    from .features.knowledge.tables import KnFact
    from .outbox import enqueue

    forwarded = 0
    for event in pending_events(service.db):
        with service.db.exclusive() as session:
            correlation = event.get("correlation") or {}
            review = session.get(Review, correlation.get("review_id", ""))
            run = session.get(Run, review.run_id) if review else None
            fact = session.get(KnFact, event["fact_id"])
            if (
                review
                and review.answer is None
                and run
                and fact
                and fact.layer == "confirmed"
                and not fact.superseded_by
                and not fact.retired
                and valid_now(fact)
                and correlation.get("run_id", run.id) == run.id
                and correlation.get("job_id", run.job_id) == run.job_id
                and run.state in {"waiting_answer", "needs_review"}
            ):
                from .features.knowledge import identify

                job = session.get(Job, run.job_id)
                # Do not acquire another exclusive transaction here.
                from .features.knowledge import employers

                employer = employers.resolve(session, job.company, job.url, create=False)
                aliases = employer.aliases if employer else ()
                same_question = identify(review.question, aliases).key == event["key"]
                same_employer = fact.scope == "global" or (employer and fact.employer_id == employer.id)
                value = (fact.value or {}).get("v")
                answer = ("Yes" if value else "No") if isinstance(value, bool) else str(value)
                if (
                    same_question
                    and same_employer
                    and value is not None
                    and (not review.options or answer in review.options)
                ):
                    review.answer = answer
                    enqueue(session, "fact_resume", event["id"], {"review_id": review.id}, run_id=run.id)
                    forwarded += 1
        ack_event(service.db, event["id"])
    return forwarded
