"""/api/v2/resumes/tailor and /ats: the tailoring screen's whole contract, for any registered mode."""

import asyncio
import copy
import os

from fastapi import HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from .._store import digest, new_id, now, sha256_bytes
from . import ats, choice, editor, evidence, modes
from .compiler import compile_pdf
from .layout import fit
from .rules import check_rules, doc_plain, draft_leaks, live_entries
from .select import supported_terms
from .tables import RsArtifact, RsTailorChoice
from .validate import parse_check


class ChoiceInput(BaseModel):
    mode: str = Field(max_length=40)


class ChoicesInput(BaseModel):
    job_ids: list[str] = Field(default_factory=list, max_length=200)
    mode: str = Field(max_length=40)


class ApprovalInput(BaseModel):
    approval: bool


class SaveInput(BaseModel):
    doc: editor.ResumeDoc
    removed_projects: list[str] = Field(default_factory=list, max_length=30)


class ApproveInput(BaseModel):
    artifact_id: str
    apply: bool = True


class ScoreInput(BaseModel):
    job_id: str
    doc: editor.ResumeDoc | None = None
    text: str | None = Field(default=None, max_length=60000)


def _evidence_text(profile: dict, resume_text: str, *docs) -> tuple:
    inv = evidence.build(profile, resume_text)
    text = " ".join([*(doc_plain(d) for d in docs if d is not None), resume_text or "", inv.vocabulary or ""])
    return inv, text


def _job(db, job_id):
    from ...db import Job, Resume

    with db.session() as s:
        job = s.get(Job, job_id)
        if job is None:
            raise HTTPException(404, "Job not found")
        wanted = (db.get_setting("application_resume_policy", {}) or {}).get("resume_id")
        rid = wanted or job.resume_id
        resume = s.get(Resume, rid) if rid else None
        resume = resume or s.scalar(select(Resume).where(Resume.demo.is_(False)).order_by(Resume.created_at.desc()).limit(1))
        return (
            {"id": job.id, "company": job.company, "title": job.title, "location": job.location, "url": job.url, "description": job.description or "", "status": job.status, "resume_id": job.resume_id},
            (resume.text or "") if resume else "",
            resume.name if resume else None,
        )


def scored(terms, text, evidence_text) -> dict:
    """The score, with what the browser needs to rescore on each keystroke: a term's status after an edit depends only on `in_evidence`."""
    result = ats.score(terms, text, evidence_text)
    for k in result["keywords"]:
        k["in_evidence"] = ats.matches(k["pattern"], evidence_text)
    return result


def estimates(db, config, doc_chars: int, posting_chars: int, has_master: bool) -> dict:
    """What each mode should cost in model calls, before it runs."""
    from ...agent_settings import for_role
    from ...models import token_cost

    role = for_role(db, config, "answers")
    out = {}
    for row in modes.listing():
        spec = modes.get(row["id"])
        if spec is None or spec.estimate_tokens is None:
            continue
        prompt, completion = spec.estimate_tokens(doc_chars, posting_chars)
        if not has_master:  # The first tailoring also transcribes the resume once.
            prompt += int(doc_chars / 3.5)
            completion += int(doc_chars / 3.0)
        out[row["id"]] = round(token_cost(role, prompt, completion), 6)
    return out


def view(db, config, job_id: str) -> dict:
    from .master import current_master

    job, resume_text, resume_name = _job(db, job_id)
    profile = db.get_setting("profile", {}) or {}
    with db.session() as s:
        row = s.get(RsTailorChoice, job_id)
        artifact = s.get(RsArtifact, row.artifact_id) if row and row.artifact_id else None
        failed = None
        if row and row.state == "failed":
            failed = s.scalar(
                select(RsArtifact).where(RsArtifact.job_id == job_id, RsArtifact.status == "failed").order_by(RsArtifact.created_at.desc())
            )
        v = copy.deepcopy(artifact.validation or {}) if artifact else {}
        artifact_view = artifact and {
            "id": artifact.id,
            "pdf_sha": artifact.pdf_sha,
            "model_calls": artifact.model_calls,
            "cost": artifact.cost,
            "finished_at": artifact.finished_at,
            "policy_version": artifact.policy_version,
        }
        stale = bool(artifact) and not choice.artifact_current(s, artifact, _JobLike(job))
        blocked = choice.content_problem(s, artifact) if artifact else None
        project_changes_pending = False
        if artifact and (v.get("report") or {}).get("workspace_id"):
            from .aggressive import store

            workspace = s.get(store.Workspace, v["report"]["workspace_id"])
            project_changes_pending = workspace is None or workspace.revision != v["report"].get("workspace_revision")
        choice_view = row and {
            "mode": row.mode,
            "state": row.state,
            "error": row.error or {},
            "artifact_id": row.artifact_id,
            "approved_artifact_id": row.approved_artifact_id,
            "approved_at": row.approved_at,
            "approved_how": row.approved_how,
        }
        rule_problems = ((failed.validation or {}).get("rules") or {}).get("problems", []) if failed else []
        failure = choice.plain_failure(row.error, rule_problems) if row and row.state == "failed" else None
    source_doc = editor.ResumeDoc.model_validate(v["source_doc"]) if v.get("source_doc") else None
    doc = editor.ResumeDoc.model_validate(v["editor"]["doc"]) if v.get("editor", {}).get("doc") else None
    inv, evidence_text = _evidence_text(profile, resume_text, source_doc)
    terms = v.get("ats_terms") or ats.terms(job["description"], job["title"], supported_terms(inv))
    source_text = doc_plain(source_doc) if source_doc else resume_text
    now_text = doc_plain(doc) if doc else source_text
    master = current_master(db, approved_only=True)
    return {
        "job": job,
        "resume_name": resume_name,
        "modes": modes.listing(),
        "approval_required": choice.approval_required(db),
        "choice": choice_view,
        "stale": stale,
        "approval_block": {"code": blocked[0], "message": blocked[1]} if blocked else None,
        "project_changes_pending": project_changes_pending,
        "artifact": artifact_view,
        "source_doc": source_doc.model_dump() if source_doc else None,
        "doc": doc.model_dump() if doc else None,
        "ats": scored(terms, now_text, evidence_text),
        "ats_source": ats.score(terms, source_text, evidence_text)["score"],
        "ats_pdf": (v.get("ats") or {}).get("pdf_score"),
        "rules": v.get("rules"),
        "preview_rules": v.get("preview_rules") or v.get("rules"),
        "rule_problems": rule_problems,
        "failure": failure,
        "started": job["status"] in choice.STARTED,
        "in_line": db.get_setting("owner_apply:" + job_id) is not None,
        "parse": v.get("parse"),
        "quality": v.get("quality"),
        "report": v.get("report") or {},
        "drafts": v.get("drafts") or [],
        "origin": v.get("origin"),
        "pages": v.get("pages"),
        "cost": v.get("cost"),
        "removed_projects": v.get("removed_projects") or [],
        "estimates": estimates(db, config, len(source_text), len(job["description"]), master is not None),
    }


class _JobLike:
    def __init__(self, job):
        self.description = job["description"]
        self.resume_id = job["resume_id"]


def editor_rules(db, data, pdf: bytes) -> dict:
    """A job's resume saved from the full editor meets the same rules as one saved from the review screen."""
    from .master import master_doc

    if data.latex is not None or data.doc is None:
        return {
            "ok": False,
            "problems": [
                {
                    "code": "hand_latex",
                    "where": "pdf",
                    "message": "A resume edited as LaTeX can't be checked against the resume rules, so it can't be sent as tailored. Edit it in the fields instead.",
                }
            ],
            "warnings": [],
        }
    validation = {}
    if data.base_artifact_id:
        with db.session() as s:
            base = s.get(RsArtifact, data.base_artifact_id)
            validation = copy.deepcopy(base.validation or {}) if base else {}
    if validation.get("source_doc"):
        source = editor.ResumeDoc.model_validate(validation["source_doc"])
    else:
        master = master_doc(db)
        if not master:
            return {"ok": False, "problems": [{"code": "no_source", "where": "pdf", "message": "There's no source resume to check this against."}], "warnings": []}
        source = master[0]
    return check_rules(
        data.doc,
        source,
        pdf=pdf,
        allowed_skills=validation.get("allowed_skills") or (db.get_setting("profile", {}) or {}).get("skills") or [],
        added_projects=[editor.Entry.model_validate(e) for e in validation.get("added_projects") or []],
        removed_projects=validation.get("removed_projects") or [],
        unconfirmed=validation.get("drafts") or [],
        edited=[b.id for s in data.doc.sections for e in s.entries for b in e.bullets],
    )


def add(r, service) -> None:
    db, config = service.db, service.config

    @r.get("/tailor/modes")
    def tailor_modes():
        return {"modes": modes.listing(), "approval_required": choice.approval_required(db)}

    @r.get("/tailor/settings")
    def tailor_settings():
        return {"approval": choice.approval_required(db)}

    @r.put("/tailor/settings")
    def set_tailor_settings(data: ApprovalInput):
        return choice.set_approval(db, data.approval)

    @r.get("/tailor/choices")
    def tailor_choices(job_ids: str = ""):
        return {"choices": choice.many(db, [j for j in job_ids.split(",") if j][:200]), "modes": modes.listing()}

    @r.put("/tailor/choices")
    async def set_tailor_choices(data: ChoicesInput):
        """The same choice for several rows; each job builds in turn."""
        done = {}
        for job_id in data.job_ids:
            try:
                done[job_id] = await asyncio.to_thread(choice.choose, db, job_id, data.mode)
            except ValueError as exc:
                done[job_id] = {"error": str(exc)}
            except LookupError as exc:
                raise HTTPException(409, str(exc)) from None
            if (done[job_id] or {}).get("state") == "preparing":
                choice.start(db, config, job_id)
        return {"choices": done}

    @r.get("/tailor/job/{job_id}")
    async def tailor_job(job_id: str):
        if await asyncio.to_thread(choice.reclaim, db, job_id):
            choice.start(db, config, job_id)
        return await asyncio.to_thread(view, db, config, job_id)

    @r.put("/tailor/job/{job_id}")
    async def choose_for_job(job_id: str, data: ChoiceInput):
        try:
            chosen = await asyncio.to_thread(choice.choose, db, job_id, data.mode)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from None
        except LookupError as exc:
            raise HTTPException(409, str(exc)) from None
        if chosen and chosen["state"] == "preparing":
            choice.start(db, config, job_id)
        return {"choice": chosen}

    @r.post("/tailor/job/{job_id}/retry")
    async def retry(job_id: str):
        """Tailor this job again from the source, setting aside what's there. A new version needs a new review."""
        from ...db import Job

        with db.exclusive() as s:
            row = s.get(RsTailorChoice, job_id)
            if row is None:
                raise HTTPException(404, "This job isn't set to a tailored resume")
            job = s.get(Job, job_id)
            if job is not None and job.status in choice.STARTED:
                raise HTTPException(409, choice.ALREADY_STARTED)
            row.state, row.error, row.artifact_id, row.lease_until = "preparing", {}, None, choice.lease()
            row.approved_artifact_id = row.approved_sha = row.approved_at = row.approved_how = None
        choice.start(db, config, job_id, fresh=True)
        return {"state": "preparing"}

    @r.post("/tailor/job/{job_id}/save")
    async def save(job_id: str, data: SaveInput):
        """The applicant's edit, compiled and checked, becomes a new immutable version. Anything already approved no longer is."""
        from ...db import Job

        with db.session() as s:
            started = s.get(Job, job_id)
            if started is not None and started.status in choice.STARTED:
                raise HTTPException(409, choice.ALREADY_STARTED)
            row = s.get(RsTailorChoice, job_id)
            base = s.get(RsArtifact, row.artifact_id) if row and row.artifact_id else None
            if base is None or base.status != "ready":
                raise HTTPException(409, "There's no tailored version to edit yet")
            if (base.validation.get("report") or {}).get("workspace_id"):
                from .aggressive.guards import send_problem as project_problem
                problem = project_problem(s, base)
                if problem and problem[0] == choice.STALE:
                    raise HTTPException(409, "Update the project PDF before editing this resume.")
            v = copy.deepcopy(base.validation or {})
            identity = {
                "source_resume_id": base.source_resume_id,
                "source_resume_sha": base.source_resume_sha,
                "profile_digest": base.profile_digest,
                "job_identity": base.job_identity,
                "description_sha": base.description_sha,
                "employer_id": base.employer_id,
            }
        source = editor.ResumeDoc.model_validate(v["source_doc"])
        before = editor.ResumeDoc.model_validate(v["editor"]["doc"])
        style = editor.Style.model_validate(v["editor"]["style"])
        job, resume_text, _ = await asyncio.to_thread(_job, db, job_id)
        profile = db.get_setting("profile", {}) or {}
        _inv, evidence_text = _evidence_text(profile, resume_text, source)
        known = {e.id for s_ in source.sections if s_.kind == "projects" for e in s_.entries}
        removed = [x for x in data.removed_projects if x in known]
        if v.get("mode") == "aggressive" and set(removed) != set(v.get("removed_projects") or []):
            raise HTTPException(409, "Change the originals in Choose your projects, then update the PDF.")
        was = {b.id: b.text for s_ in before.sections for e in s_.entries for b in e.bullets}
        edited = [b.id for s_ in data.doc.sections for e in s_.entries for b in e.bullets if was.get(b.id) != b.text]
        had = {s_.id: s_.text for s_ in before.sections}
        edited += [s_.id for s_ in data.doc.sections if s_.kind in {"text", "skills"} and had.get(s_.id) != s_.text]
        added = [editor.Entry.model_validate(e) for e in v.get("added_projects") or []]
        previews = [editor.Entry.model_validate(e) for e in v.get("preview_projects") or []]
        # Decide the structure first: there's no point compiling what can't be saved, and the answer names the bullet to fix.
        first, _ = modes.review_checks(data.doc, source, allowed_skills=v.get("allowed_skills") or [], added_projects=added, preview_projects=previews, removed_projects=removed, unconfirmed=v.get("drafts") or [], vocabulary=evidence_text, edited=edited)
        if not first["ok"]:
            return JSONResponse({"detail": first["problems"][0]["message"], "problems": first["problems"], "warnings": first["warnings"]}, status_code=422)
        laid = await fit(data.doc, style, compile_pdf)
        compiled = laid["compiled"]
        if not compiled.ok:
            return JSONResponse({"detail": compiled.detail, "problems": [{"code": "compile", "where": "pdf", "message": compiled.detail}], "warnings": []}, status_code=422)
        preview_rules, rules = modes.review_checks(data.doc, source, pdf=compiled.pdf, allowed_skills=v.get("allowed_skills") or [], added_projects=added, preview_projects=previews, removed_projects=removed, unconfirmed=v.get("drafts") or [], vocabulary=evidence_text, edited=edited)
        if not preview_rules["ok"]:
            return JSONResponse({"detail": preview_rules["problems"][0]["message"], "problems": preview_rules["problems"], "warnings": preview_rules["warnings"]}, status_code=422)
        parsed = parse_check(compiled.pdf, data.doc)
        if not parsed["ok"]:
            failed = "; ".join(i["label"] for i in parsed["items"] if not i["ok"])
            raise HTTPException(422, f"This version wouldn't read cleanly in an employer's system, so it wasn't saved. What didn't read: {failed}.")
        terms = v.get("ats_terms") or []
        from . import gates

        # A term nothing supports, which they typed themselves. It stays, because it's their resume, and they're told plainly.
        typed = ats.adds_forbidden(doc_plain(data.doc), evidence_text, ats.forbidden(ats.score(terms, doc_plain(source), evidence_text)))
        if typed:
            rules["warnings"].append({"code": "your_statement", "where": "resume", "message": "Nothing else you've given AXIOM says you have: " + ", ".join(typed[:8]) + ". Keep it only if it's true."})

        v.update(
            editor={"doc": data.doc.model_dump(), "style": laid["style"].model_dump()},
            rules=rules,
            preview_rules=preview_rules,
            parse={k: x for k, x in parsed.items() if k != "text"},
            quality=gates.quality(data.doc),
            ats={
                "score": ats.score(terms, doc_plain(data.doc), evidence_text)["score"],
                "pdf_score": ats.score(terms, parsed["text"], evidence_text)["score"],
                "source_score": (v.get("ats") or {}).get("source_score"),
            },
            pages=parsed["pages"],
            removed_projects=removed,
            edited_by="applicant",
            latex=editor.render(data.doc, laid["style"]),
            report={**(v.get("report") or {}), "fill": laid["fill"], "gap_pt": laid["gap_pt"], "overflow": laid["overflow"]},
        )
        artifact_id = new_id("ta_")
        rel = os.path.join("tailored", artifact_id + ".pdf")
        os.makedirs(os.path.join(str(config.data_dir), "tailored"), mode=0o700, exist_ok=True)
        path = os.path.join(str(config.data_dir), rel)
        with open(path, "wb") as f:
            f.write(compiled.pdf)
        os.chmod(path, 0o600)
        pdf_sha = sha256_bytes(compiled.pdf)
        approval = choice.approval_required(db) or not rules["ok"]
        with db.exclusive() as s:
            current = s.get(RsTailorChoice, job_id)
            if current is None or current.artifact_id != base.id:
                raise HTTPException(409, "This resume changed while saving. Reload the review.")
            if (v.get("report") or {}).get("workspace_id"):
                from .aggressive import store
                from .aggressive.integration import real_document
                workspace = s.get(store.Workspace, v["report"]["workspace_id"])
                if workspace is None or workspace.revision != v["report"].get("workspace_revision"):
                    raise HTTPException(409, "Project choices changed while saving. Update the PDF again.")
                workspace.payload = {**workspace.payload, "report": {**workspace.payload.get("report", {}), "tailored_doc": real_document(store.view(workspace), v).model_dump()}}
            s.add(
                RsArtifact(
                    id=artifact_id,
                    cache_key=digest([modes.REVIEW_POLICY, job_id, pdf_sha])[:40] + artifact_id[-8:],
                    status="ready",
                    job_id=job_id,
                    policy_version=modes.REVIEW_POLICY,
                    pdf_sha=pdf_sha,
                    pdf_path=rel,
                    text=parsed["text"][:20000],
                    selection=editor.selection_of(data.doc),
                    validation=v,
                    finished_at=now(),
                    **identity,
                )
            )
            row = s.get(RsTailorChoice, job_id)
            row.artifact_id, row.updated_at = artifact_id, now()
            row.state = "review" if approval else "approved"
            row.approved_artifact_id = None if approval else artifact_id
            row.approved_sha = None if approval else pdf_sha
            row.approved_at = None if approval else now()
            row.approved_how = None if approval else "setting_off"
        return await asyncio.to_thread(view, db, config, job_id)

    @r.post("/tailor/job/{job_id}/approve")
    async def approve(job_id: str, data: ApproveInput):
        """Yes to this exact file, then into the queue: the dispatcher starts it in its turn, and every other guard still decides."""
        try:
            result = await asyncio.to_thread(choice.approve, db, config, job_id, data.artifact_id)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None
        message = "Approved."
        if data.apply:
            from ...owner_apply import enqueue

            try:
                placed = await asyncio.to_thread(enqueue, service, job_id)
                message = "Approved and added to the queue." if placed.get("state") == "waiting" else "Approved. " + placed.get("message", "")
            except ValueError as exc:
                message = f"Approved, but it couldn't join the queue: {exc}"
        return {**result, "message": message}

    @r.post("/ats/score")
    def ats_score(data: ScoreInput):
        """The server's number for the browser's text: the same patterns, the same sum, so the two agree."""
        job, resume_text, _ = _job(db, data.job_id)
        profile = db.get_setting("profile", {}) or {}
        inv, evidence_text = _evidence_text(profile, resume_text)
        terms = ats.terms(job["description"], job["title"], supported_terms(inv))
        with db.session() as s:
            row = s.get(RsTailorChoice, data.job_id)
            base = s.get(RsArtifact, row.artifact_id) if row and row.artifact_id else None
            if base and (base.validation or {}).get("ats_terms"):
                terms = base.validation["ats_terms"]
        text = data.text if data.text is not None else (doc_plain(data.doc) if data.doc else resume_text)
        return scored(terms, text, evidence_text)


__all__ = ["add", "editor_rules", "live_entries", "draft_leaks"]
