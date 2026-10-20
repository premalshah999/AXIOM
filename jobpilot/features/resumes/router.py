"""/api/v2/resumes — artifacts, evidence inventory, prepare/cancel."""

import asyncio
import os

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel
from sqlalchemy import func, select

from . import editor, evidence, pipeline
from . import master as master_mod
from .tables import RsArtifact

_TASKS: set = set()


class EditorInput(BaseModel):
    doc: editor.ResumeDoc | None = None
    style: editor.Style = editor.Style()
    latex: str | None = None  # hand-edited source; replaces doc/style when present
    job_id: str | None = None
    base_artifact_id: str | None = None


class PrepareInput(BaseModel):
    job_id: str
    source_resume_id: str | None = None


def router(service) -> APIRouter:
    db, config = service.db, service.config
    r = APIRouter(prefix="/api/v2/resumes", tags=["resumes"])

    @r.get("/artifacts")
    def artifacts(status: str | None = None, job_id: str | None = None, page: int = 1, size: int = 25):
        size, page = max(1, min(100, size)), max(1, page)
        with db.session() as s:
            q = select(RsArtifact)
            if status:
                q = q.where(RsArtifact.status == status)
            if job_id:
                q = q.where(RsArtifact.job_id == job_id)
            total = s.scalar(select(func.count()).select_from(q.subquery()))
            rows = s.scalars(q.order_by(RsArtifact.created_at.desc()).offset((page - 1) * size).limit(size))
            items = [
                {k: v for k, v in pipeline.record(x).model_dump().items() if k not in {"text", "pdf_path"}}
                for x in rows
            ]
        return {"items": items, "total": total, "page": page, "size": size}

    @r.get("/artifacts/{artifact_id}")
    def artifact(artifact_id: str):
        with db.session() as s:
            row = s.get(RsArtifact, artifact_id)
            if not row:
                raise HTTPException(404)
            out = pipeline.record(row).model_dump()
        out.pop("pdf_path", None)
        return out

    @r.get("/artifacts/{artifact_id}/pdf")
    def pdf(artifact_id: str):
        with db.session() as s:
            row = s.get(RsArtifact, artifact_id)
            if not row or row.status != "ready" or not row.pdf_path:
                raise HTTPException(404)
            path = os.path.join(str(config.data_dir), row.pdf_path)
        if not os.path.exists(path):
            raise HTTPException(404)
        return FileResponse(path, media_type="application/pdf", filename="resume.pdf")

    @r.post("/prepare")
    async def prepare(data: PrepareInput):
        task = asyncio.create_task(pipeline.prepare_resume(db, config, data.job_id, data.source_resume_id))
        _TASKS.add(task)
        task.add_done_callback(_TASKS.discard)
        await asyncio.sleep(0.05)  # Let cache hits and immediate failures answer synchronously.
        if task.done():
            return task.result().model_dump()
        return {"status": "building"}

    @r.post("/artifacts/{artifact_id}/cancel")
    async def cancel(artifact_id: str):
        return {"cancelled": pipeline.cancel(db, artifact_id)}

    @r.get("/evidence")
    def inventory():
        """The applicant's supported material, as tailoring sees it (no contact values)."""
        from ...db import Resume

        with db.session() as s:
            resume = s.scalar(select(Resume).where(Resume.demo.is_(False)))
            text = resume.text if resume else ""
        inv = evidence.build(db.get_setting("profile", {}) or {}, text)
        return {
            "roles": [r.__dict__ for r in inv.roles],
            "education": [
                {k: e.get(k) for k in ("row", "school", "degree", "field_of_study", "start", "end")}
                for e in inv.education
            ],
            "projects": [p.__dict__ for p in inv.projects],
            "units": [u.__dict__ for u in inv.units],
            "skills": inv.skills,
        }

    # ---------- editor ----------
    def source_resume():
        from ...db import Resume

        with db.session() as s:
            row = s.scalar(select(Resume).where(Resume.demo.is_(False)))
            return (row.id, row.sha256, row.text or "") if row else ("", "", "")

    def tex_for(data: EditorInput) -> str:
        if data.latex is not None:
            problems = editor.check_source(data.latex)
            if problems:
                raise HTTPException(422, "; ".join(problems))
            return data.latex
        if data.doc is None:
            raise HTTPException(422, "Send a document or LaTeX source")
        return editor.render(data.doc, data.style)

    @r.get("/editor/templates")
    def templates():
        return {
            "templates": [{"id": k, **v} for k, v in editor.TEMPLATES.items()],
            "fonts": [
                {"id": k, "label": v[0], "available": k in editor.available_fonts()}
                for k, v in editor.FONTS.items()
            ],
            "defaults": editor.Style().model_dump(),
        }

    @r.get("/editor/document")
    def document(artifact_id: str | None = None, master: bool = False):
        rid, _, text = source_resume()
        if master or not artifact_id:
            row = master_mod.current_master(db)
            if row is not None:
                doc = editor.ResumeDoc.model_validate(row.doc)
                style = editor.Style.model_validate(row.style)
                return {
                    "doc": doc.model_dump(),
                    "style": style.model_dump(),
                    "latex": editor.render(doc, style),
                    "job_id": None,
                    "base_artifact_id": None,
                    "source_edited": False,
                    "master": master_mod.master_view(row),
                }
            if master:
                raise HTTPException(404, "No master yet. Import it from your resume PDF first.")
        inv = evidence.build(db.get_setting("profile", {}) or {}, text)
        selection, job_id = None, None
        if artifact_id:
            with db.session() as s:
                row = s.get(RsArtifact, artifact_id)
                if not row:
                    raise HTTPException(404)
                selection, job_id = row.selection or [], row.job_id
                saved = (row.validation or {}).get("editor")
            if saved and saved.get("doc"):
                doc = editor.ResumeDoc.model_validate(saved["doc"])
                style = editor.Style.model_validate(saved.get("style") or {})
                return {
                    "doc": doc.model_dump(),
                    "style": style.model_dump(),
                    "latex": saved.get("latex") or editor.render(doc, style),
                    "job_id": job_id,
                    "base_artifact_id": artifact_id,
                    "source_edited": bool(saved.get("latex")),
                }
        doc, style = editor.from_inventory(inv, selection), editor.Style()
        return {
            "doc": doc.model_dump(),
            "style": style.model_dump(),
            "latex": editor.render(doc, style),
            "job_id": job_id,
            "base_artifact_id": artifact_id,
            "source_edited": False,
        }

    @r.post("/editor/render")
    async def render_source(data: EditorInput):
        return {"latex": tex_for(data)}

    @r.post("/editor/compile")
    async def compile_preview(data: EditorInput):
        import time

        from pypdf import PdfReader
        import io

        tex = tex_for(data)
        started = time.perf_counter()
        result = await pipeline.compile_pdf(tex, timeout=25, concurrency=3)
        ms = round((time.perf_counter() - started) * 1000)
        if not result.ok:
            message = result.detail or "LaTeX could not compile this document"
            return JSONResponse(
                {"detail": message, "errors": result.errors or [], "code": result.code, "ms": ms},
                status_code=422,
            )
        pages = len(PdfReader(io.BytesIO(result.pdf)).pages)
        return Response(
            result.pdf,
            media_type="application/pdf",
            headers={"X-Pages": str(pages), "X-Compile-Ms": str(ms), "Cache-Control": "no-store"},
        )

    @r.post("/editor/save")
    async def save(data: EditorInput):
        from .._store import digest, new_id, now, sha256_bytes
        from .validate import check_pdf
        from ...db import Job

        tex = tex_for(data)
        result = await pipeline.compile_pdf(tex, timeout=30, concurrency=3)
        if not result.ok:
            return JSONResponse({"detail": result.detail, "errors": result.errors or []}, status_code=422)
        rid, rsha, _ = source_resume()
        identity, employer_id = "standalone", None
        pdigest, dsha = "standalone", ""
        if data.job_id:
            with db.session() as s:
                job = s.get(Job, data.job_id)
                if not job:
                    raise HTTPException(404, "Job not found")
                identity = pipeline.job_identity(job.url)
            # The same identity a tailored version carries, so a saved edit is the
            # job's current resume until the profile, posting or source changes.
            current = pipeline.current_identity(db, data.job_id)
            if current:
                rid, rsha = current["source_resume_id"], current["source_resume_sha"]
                pdigest, dsha = current["profile_digest"], current["description_sha"]
        doc = data.doc
        email = next((c for c in (doc.contact if doc else []) if "@" in c), "")
        check = check_pdf(
            result.pdf,
            name=doc.name if doc else "",
            email=email,
            bullets=[],
            keywords=[],
            max_pages=2,
            projects=False,
        )
        artifact_id = new_id("ta_")
        rel = os.path.join("tailored", artifact_id + ".pdf")
        os.makedirs(os.path.join(str(config.data_dir), "tailored"), mode=0o700, exist_ok=True)
        path = os.path.join(str(config.data_dir), rel)
        with open(path, "wb") as f:
            f.write(result.pdf)
        os.chmod(path, 0o600)
        validation = {k: v for k, v in check.items() if k != "text"}
        validation["edited_by"] = "applicant"
        validation["editor"] = {
            "doc": doc.model_dump() if doc else None,
            "style": data.style.model_dump(),
            "latex": data.latex,
        }
        with db.exclusive() as s:
            if data.base_artifact_id:
                base = s.get(RsArtifact, data.base_artifact_id)
                employer_id = base.employer_id if base else None
            s.add(
                RsArtifact(
                    id=artifact_id,
                    cache_key=digest([editor.EDITOR_VERSION, data.job_id, tex])[:40] + artifact_id[-8:],
                    status="ready",
                    source_resume_id=rid,
                    source_resume_sha=rsha,
                    profile_digest=pdigest,
                    job_identity=identity,
                    job_id=data.job_id,
                    employer_id=employer_id,
                    description_sha=dsha,
                    policy_version=editor.EDITOR_VERSION,
                    pdf_sha=sha256_bytes(result.pdf),
                    pdf_path=rel,
                    text=check["text"][:20000],
                    selection=editor.selection_of(doc) if doc and data.latex is None else [],
                    validation=validation,
                    finished_at=now(),
                )
            )
        return {
            "artifact_id": artifact_id,
            "pages": check["pages"],
            "problems": check["problems"],
            "warnings": check["warnings"],
        }

    # ---------- master resume ----------
    class MasterSave(BaseModel):
        doc: editor.ResumeDoc
        style: editor.Style = editor.Style()

    class Approve(BaseModel):
        accept_differences: bool = False

    @r.get("/master")
    def master_status():
        row = master_mod.current_master(db)
        approved = master_mod.current_master(db, approved_only=True)
        source, _ = master_mod.source_pdf(db, config)
        return {
            "master": master_mod.master_view(row),
            "approved_id": approved.id if approved else None,
            "source": {"id": source["id"], "name": source["name"]} if source else None,
            "policy": db.get_setting("application_resume_policy", {}) or {"mode": "tailored"},
        }

    @r.post("/master/import")
    async def master_import():
        if not getattr(config, "llm_api_key", ""):
            raise HTTPException(409, "Importing needs a configured language model")
        from ..knowledge.writing import configured_model

        try:
            return await master_mod.import_master(db, config, configured_model(db, config))
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None

    @r.put("/master")
    async def master_save(data: MasterSave):
        try:
            return await master_mod.save_master(db, config, data.doc, data.style)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None

    @r.post("/master/{master_id}/approve")
    async def master_approve(master_id: str, data: Approve):
        try:
            return master_mod.approve(db, master_id, data.accept_differences)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None

    return r
