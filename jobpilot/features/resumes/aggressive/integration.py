"""Apply project choices to an immutable review artifact without a model call."""

import copy
from pathlib import Path

from ..._store import digest, new_id, now, sha256_bytes
from .. import ats, choice, editor, evidence, gates, modes
from ..compiler import compile_pdf
from ..rules import doc_plain
from ..tables import RsArtifact, RsTailorChoice
from . import quality, store
from .pipeline import compile_and_check, with_projects


def real_document(workspace, validation=None):
    """Keep applicant edits while removing the previous draft selections."""
    validation = validation or {}
    report = workspace.get("report") or {}
    source = editor.ResumeDoc.model_validate(workspace["source_doc"])
    doc = editor.ResumeDoc.model_validate(
        (validation.get("editor") or {}).get("doc") or report.get("tailored_doc") or workspace["source_doc"]
    )
    generated = {d["id"] for d in workspace["drafts"]}
    for section in doc.sections:
        if section.kind == "projects":
            section.entries = [e for e in section.entries if e.id not in generated]
    for original in source.sections:
        if not original.include or original.kind != "projects":
            continue
        section = next((s for s in doc.sections if s.id == original.id), None)
        if section is None:
            section = original.model_copy(deep=True)
            doc.sections.append(section)
        section.include = True
        existing = {e.id: e for e in section.entries}
        for entry in original.entries:
            if not entry.include:
                continue
            if entry.id not in existing:
                section.entries.append(entry.model_copy(deep=True))
            else:
                existing[entry.id].include = True
    return doc


async def refresh(service, job_id, workspace_id, revision, *, compile_fn=compile_pdf):
    from ....db import Job, Resume

    db, config = service.db, service.config
    workspace = store.get(db, workspace_id)
    if workspace["revision"] != revision:
        raise ValueError("Project choices changed. Reload this review before updating its PDF.")
    with db.session() as s:
        selected_choice = s.get(RsTailorChoice, job_id)
        base = (
            s.get(RsArtifact, selected_choice.artifact_id)
            if selected_choice and selected_choice.artifact_id
            else None
        )
        job = s.get(Job, job_id)
        if (
            base is None
            or job is None
            or base.status != "ready"
            or (base.validation.get("report") or {}).get("workspace_id") != workspace_id
        ):
            raise ValueError("These projects don't belong to this job's current tailored resume.")
        if job.status in choice.STARTED:
            raise ValueError("This application has already started, so its resume can't change now.")
        resume = s.get(Resume, base.source_resume_id)
        if (
            not choice.artifact_current(s, base, job)
            or resume is None
            or resume.sha256 != base.source_resume_sha
        ):
            raise ValueError("Your source resume, profile or posting changed. Tailor this job again.")
        base_id = base.id
        identity = {
            k: getattr(base, k)
            for k in (
                "source_resume_id",
                "source_resume_sha",
                "profile_digest",
                "job_identity",
                "job_id",
                "employer_id",
                "description_sha",
            )
        }
        validation = copy.deepcopy(base.validation)

    source = modes.SourceResume(
        id=identity["source_resume_id"],
        sha=identity["source_resume_sha"],
        text=resume.text or "",
        doc=editor.ResumeDoc.model_validate(validation["source_doc"]),
        style=editor.Style.model_validate(validation["editor"]["style"]),
    )
    real = real_document(workspace, validation)
    doc, selected = with_projects(real, workspace)
    allowed = validation.get("allowed_skills") or []
    profile = db.get_setting("profile", {}) or {}
    inv = evidence.build(profile, source.text)
    ctx = modes.ModeContext(db, config, None, compile_fn, Path(config.data_dir), inv)
    built = await compile_and_check(
        doc, source, ctx, selected, workspace, allowed, editor.tailored_style(source.style)
    )
    pending = [
        {"id": d["id"], "title": d["title"], "bullets": d["bullets"], "confirmed": False}
        for d in selected
        if not store.confirmed(d)
    ]
    added = [store.entry(d) for d in selected if store.confirmed(d)]
    preview_projects = [store.entry(d) for d in selected if not store.confirmed(d)]
    preview_rules, send_rules = modes.review_checks(
        doc,
        source.doc,
        pdf=built["pdf"],
        added_projects=added,
        preview_projects=preview_projects,
        unconfirmed=pending,
        removed_projects=workspace["removed_projects"],
        allowed_skills=allowed,
    )
    if not preview_rules["ok"]:
        raise ValueError(preview_rules["problems"][0]["message"])
    parsed = built["parsed"]
    report = {
        **validation.get("report", {}),
        "workspace_revision": revision,
        "requires_project_approval": bool(pending),
        "removed_projects": workspace["removed_projects"],
        "preview_rules": preview_rules,
        "send_rules": send_rules,
        "tailored_doc": real.model_dump(),
        "style": built["style"].model_dump(),
        **quality.coverage(parsed["text"], (workspace.get("brief") or {}).get("hard_keywords", [])),
        "page_fill": {
            "gap_pt": built["gap_pt"],
            "compiles": built["compiles"],
            "overflow": built["overflow"],
        },
        "overflow_message": f"This resume runs to {parsed['pages']} pages. Nothing was cut to fit."
        if built["overflow"]
        else "",
    }
    terms = validation.get("ats_terms") or []
    evidence_text = " ".join([source.text, doc_plain(source.doc), inv.vocabulary])
    validation.update(
        editor={"doc": doc.model_dump(), "style": built["style"].model_dump()},
        rules=send_rules,
        preview_rules=preview_rules,
        parse={k: v for k, v in parsed.items() if k != "text"},
        quality=gates.quality(doc),
        ats={
            **validation.get("ats", {}),
            "score": ats.score(terms, doc_plain(doc), evidence_text)["score"],
            "pdf_score": ats.score(terms, parsed["text"], evidence_text)["score"],
        },
        added_projects=[e.model_dump() for e in added],
        preview_projects=[e.model_dump() for e in preview_projects],
        drafts=pending,
        removed_projects=workspace["removed_projects"],
        pages=parsed["pages"],
        latex=built["latex"],
        report=report,
        cost={"usd": 0, "calls": 0, "prompt_tokens": 0, "completion_tokens": 0},
    )
    artifact_id = new_id("ta_")
    relative = Path("tailored") / f"{artifact_id}.pdf"
    path = Path(config.data_dir) / relative
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_bytes(built["pdf"])
    path.chmod(0o600)
    try:
        with db.exclusive() as s:
            current = s.get(RsTailorChoice, job_id)
            stored = s.get(store.Workspace, workspace_id)
            current_job = s.get(Job, job_id)
            if (
                current is None
                or current.artifact_id != base_id
                or stored is None
                or stored.revision != revision
                or current_job is None
                or current_job.status in choice.STARTED
            ):
                raise ValueError("This review changed while compiling. Reload and update the PDF again.")
            latest_source = s.get(Resume, base.source_resume_id)
            if (
                not choice.artifact_current(s, base, current_job)
                or latest_source is None
                or latest_source.sha256 != base.source_resume_sha
            ):
                raise ValueError("Your source resume, profile or posting changed. Tailor this job again.")
            family = validation.get("generation_cache_key") or digest([workspace_id, "generation"])
            # Refreshing the same revision is safe: preserve prior artifacts and
            # move only their cache pointer, as prepare_resume(fresh=True) does.
            from sqlalchemy import select

            for previous in s.scalars(select(RsArtifact).where(RsArtifact.cache_key == family)):
                previous.cache_key += ":superseded:" + previous.id
            s.flush()
            s.add(
                RsArtifact(
                    id=artifact_id,
                    cache_key=family,
                    status="ready",
                    policy_version=modes.REVIEW_POLICY,
                    pdf_sha=sha256_bytes(built["pdf"]),
                    pdf_path=str(relative),
                    text=parsed["text"][:20000],
                    selection=editor.selection_of(doc),
                    validation=validation,
                    model_calls=0,
                    cost=0,
                    finished_at=now(),
                    **identity,
                )
            )
            # A new file loses any approval. It waits for the person unless they've turned that off and it passes the send rules, which is what the row then says.
            choice.adopt(s, job_id, artifact_id, sha256_bytes(built["pdf"]), send_rules["ok"])
            stored.payload = {**stored.payload, "report": report}
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    from ..tailor_routes import view

    return view(db, config, job_id)
