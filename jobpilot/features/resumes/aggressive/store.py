"""Aggressive-owned, additive storage. Never writes a user's master or profile.

Project approval is scoped to one immutable proposal and source/posting/profile
fingerprint. Choosing originals needs no project approval. Changing a choice
requires tailoring afresh: it never mutates a previously approved PDF.
"""

import copy

from sqlalchemy import JSON, Integer, String, select, update
from sqlalchemy.orm import Mapped, mapped_column

from ..._store import FeatureBase, digest, new_id, now
from ..editor import Bullet, Entry, ResumeDoc
from .schema import Proposal


class Workspace(FeatureBase):
    __tablename__ = "rs_aggressive_workspaces"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String, unique=True)
    job_key: Mapped[str] = mapped_column(String, index=True)
    source_sha: Mapped[str] = mapped_column(String)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    revision: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[str] = mapped_column(String, default=now)


def ensure(db):
    # The mode autoloads after feature tables were created on some app versions.
    Workspace.__table__.create(db.engine, checkfirst=True)


def fingerprint(job, source, profile):
    from ..evidence import profile_digest

    document = source.doc.model_dump()
    for section in document["sections"]:
        for entry in section["entries"]:
            for bullet in entry["bullets"]:
                # Import attaches provenance without changing the source words.
                bullet.pop("source_text", None)
                bullet.pop("unit_id", None)
    return digest(
        [source.id, source.sha, document, profile_digest(profile), job.get("url"), job.get("description")]
    )


def key_for(job):
    return digest(job.get("url") or [job.get("company"), job.get("title"), job.get("description")])


def view(row):
    return {
        "id": row.id,
        "revision": row.revision,
        "source_sha": row.source_sha,
        **copy.deepcopy(row.payload),
    }


def find(db, signature):
    if db is None:
        return None
    ensure(db)
    with db.session() as s:
        row = s.scalar(select(Workspace).where(Workspace.fingerprint == signature))
        return view(row) if row else None


def get(db, workspace_id):
    ensure(db)
    with db.session() as s:
        row = s.get(Workspace, workspace_id)
        if row is None:
            raise LookupError("Project drafts not found")
        return view(row)


def create(db, job, source, profile, proposals, brief):
    signature = fingerprint(job, source, profile)
    existing = find(db, signature)
    if existing:
        return existing
    wid = new_id("ag-")
    drafts = [{"id": wid + f":p{i}", "confirmed": False, **p.model_dump()} for i, p in enumerate(proposals)]
    payload = {
        "brief": brief.model_dump(),
        "drafts": drafts,
        "selected_drafts": [d["id"] for d in drafts],
        "removed_projects": [],
        "source_doc": source.doc.model_dump(),
        "source_style": source.style.model_dump(),
        "posting": job.get("description", ""),
        "report": {},
    }
    if db is None:
        return {"id": wid, "revision": 0, "source_sha": source.sha, **payload}
    ensure(db)
    with db.exclusive() as s:
        # Check again under the writer lock: simultaneous previews share choices.
        row = s.scalar(select(Workspace).where(Workspace.fingerprint == signature))
        if row is None:
            row = Workspace(
                id=wid,
                fingerprint=signature,
                job_key=key_for(job),
                source_sha=source.sha,
                payload=payload,
                revision=0,
            )
            s.add(row)
            s.flush()
        return view(row)


def _change(db, workspace_id, revision, mutate):
    ensure(db)
    with db.exclusive() as s:
        row = s.get(Workspace, workspace_id)
        if row is None:
            raise LookupError("Project drafts not found")
        if row.revision != revision:
            raise ValueError("These drafts changed. Reload before saving your choice.")
        payload = copy.deepcopy(row.payload)
        mutate(payload)
        changed = s.execute(
            update(Workspace)
            .where(Workspace.id == workspace_id, Workspace.revision == revision)
            .values(payload=payload, revision=revision + 1)
        )
        if changed.rowcount != 1:
            raise ValueError("These drafts changed. Reload before saving your choice.")
        s.expire(row)
        return view(row)


def choose(db, workspace_id, choice):
    def mutate(payload):
        known = {d["id"] for d in payload["drafts"]}
        doc = ResumeDoc.model_validate(payload["source_doc"])
        originals = {e.id for sec in doc.sections if sec.kind == "projects" for e in sec.entries if e.include}
        if not set(choice.selected_drafts) <= known or not set(choice.removed_projects) <= originals:
            raise ValueError("Choose only projects from this resume and these drafts")
        payload.update(
            selected_drafts=list(dict.fromkeys(choice.selected_drafts)),
            removed_projects=list(dict.fromkeys(choice.removed_projects)),
        )

    return _change(db, workspace_id, choice.revision, mutate)


def confirm(db, workspace_id, draft_id, confirmation):
    def mutate(payload):
        draft = next((d for d in payload["drafts"] if d["id"] == draft_id), None)
        if draft is None:
            raise LookupError("Project draft not found")
        # Store the exact approved proposal, not a blanket approval of future text.
        draft.update(
            confirmed=True,
            approved_at=now(),
            approved_digest=digest(Proposal.model_validate(draft).model_dump()),
        )

    return _change(db, workspace_id, confirmation.revision, mutate)


def confirmed(draft):
    return draft.get("confirmed") is True and draft.get("approved_digest") == digest(
        Proposal.model_validate(draft).model_dump()
    )


def revise(db, workspace, proposals):
    """Critique can improve pending drafts; approved wording stays immutable."""

    def mutate(payload):
        for old, new in zip(payload["drafts"], proposals, strict=True):
            if not confirmed(old):
                old.update(new.model_dump(), confirmed=False)
                old.pop("approved_digest", None)
                old.pop("approved_at", None)

    if db is None:
        workspace = copy.deepcopy(workspace)
        mutate(workspace)
        workspace["revision"] += 1
        return workspace
    return _change(db, workspace["id"], workspace["revision"], mutate)


def entry(draft):
    return Entry(
        id=draft["id"],
        heading=draft["title"],
        subheading=draft["descriptor"],
        bullets=[
            Bullet(id=draft["id"] + f":b{i}", text=b, source_text=b) for i, b in enumerate(draft["bullets"])
        ],
    )


def recent(db, job, limit=15):
    if db is None:
        return []
    ensure(db)
    with db.session() as s:
        rows = s.scalars(
            select(Workspace).where(Workspace.job_key != key_for(job)).order_by(Workspace.created_at.desc())
        ).all()
        out, seen = [], set()
        for row in rows:
            if row.job_key in seen:
                continue
            seen.add(row.job_key)
            out.append(
                [
                    {k: d[k] for k in ("data_source", "archetype", "metric_shape")}
                    for d in row.payload["drafts"]
                    if d["id"] in row.payload["selected_drafts"]
                ]
            )
            if len(out) == limit:
                break
        return out


def save_report(db, workspace_id, revision, report):
    if db is None:
        return
    ensure(db)
    with db.exclusive() as s:
        row = s.get(Workspace, workspace_id)
        if row is not None and row.revision == revision:
            row.payload = {**row.payload, "report": report}
