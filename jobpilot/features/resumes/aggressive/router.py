"""Draft review endpoints. Mount router(service) alongside the shared router.

Project updates invalidate approval and can create a new review artifact. These
routes never approve or enqueue an application; the shared flow handles that.
"""

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from ..compiler import compile_pdf
from ..editor import ResumeDoc, Style, render, tailored_style
from ..layout import fit
from ..rules import check_rules
from ..validate import parse_check
from . import store
from .pipeline import with_projects
from .schema import Choice, Confirmation


class RefreshInput(BaseModel):
    job_id: str
    revision: int = Field(ge=0)


async def preview(db, workspace_id, *, compile_fn=compile_pdf):
    workspace = store.get(db, workspace_id)
    original = ResumeDoc.model_validate(workspace["source_doc"])
    report = workspace.get("report") or {}
    real = ResumeDoc.model_validate(report.get("tailored_doc") or workspace["source_doc"])
    doc, selected = with_projects(real, workspace)
    style = tailored_style(Style.model_validate(report.get("style") or workspace["source_style"]))
    fitted = await fit(doc, style, compile_fn)
    compiled = fitted["compiled"]
    if not compiled.ok:
        raise ValueError(f"{compiled.code}: {compiled.detail}")
    parsed = parse_check(compiled.pdf, doc)
    rules = check_rules(
        doc,
        original,
        pdf=compiled.pdf,
        added_projects=[store.entry(d) for d in selected],
        allowed_skills=[*report.get("source_skills", []), *report.get("allowed_additions", [])],
        removed_projects=workspace["removed_projects"],
    )
    if not rules["ok"] or not parsed["ok"]:
        raise ValueError("This preview failed the shared resume rules or ATS parse check")
    return {
        "pdf": compiled.pdf,
        "latex": render(doc, fitted["style"]),
        "rules": rules,
        "requires_project_approval": any(not store.confirmed(d) for d in selected),
        "revision": workspace["revision"],
    }


def router(service):
    db = service.db
    r = APIRouter(prefix="/api/v2/resumes/aggressive", tags=["aggressive drafts"])

    def handle(fn, *args):
        try:
            return fn(*args)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from None
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None

    @r.get("/{workspace_id}")
    def get(workspace_id: str):
        result = handle(store.get, db, workspace_id)
        result["drafts"] = [{**d, "confirmed": store.confirmed(d)} for d in result["drafts"]]
        return result

    @r.put("/{workspace_id}/choices")
    def choose(workspace_id: str, choice: Choice):
        return handle(store.choose, db, workspace_id, choice)

    @r.post("/{workspace_id}/drafts/{draft_id}/approve")
    def approve(workspace_id: str, draft_id: str, confirmation: Confirmation):
        return handle(store.confirm, db, workspace_id, draft_id, confirmation)

    @r.post("/{workspace_id}/refresh")
    async def refresh(workspace_id: str, data: RefreshInput):
        from .integration import refresh as update_pdf

        try:
            return await update_pdf(service, data.job_id, workspace_id, data.revision)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from None
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(409, str(exc)) from None

    @r.get("/{workspace_id}/preview.pdf")
    async def pdf(workspace_id: str):
        try:
            result = await preview(db, workspace_id)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from None
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(409, str(exc)) from None
        # This response is a preview only, even after project approval. The
        # shared flow still has to approve the exact PDF for an application.
        return Response(
            result["pdf"],
            media_type="application/pdf",
            headers={
                "Cache-Control": "no-store",
                "X-Project-Approval-Required": str(result["requires_project_approval"]).lower(),
                "X-Workspace-Revision": str(result["revision"]),
                "Content-Disposition": 'inline; filename="Project_Preview.pdf"',
            },
        )

    return r
