"""Legacy reviewed answers (/api/knowledge). The versioned store is /api/v2/knowledge."""

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel, Field

from ...schemas import KnowledgeInput


class QuestionnaireAnswers(BaseModel):
    # Partial: only the keys sent change. An empty value (null, "", [], {}) clears that answer.
    answers: dict = Field(default_factory=dict)
    # Fill every still-unanswered question that has a suggestion ("No" to each tax-credit question) with it.
    accept_suggestions: bool = False


def router(service) -> APIRouter:
    db = service.db
    r = APIRouter()

    @r.get("/api/knowledge")
    def knowledge_list(q: str = ""):
        from ...knowledge import search

        return {"entries": search(db, q[:200], limit=500)}

    @r.post("/api/knowledge")
    def knowledge_add(data: KnowledgeInput, background: BackgroundTasks):
        from ...knowledge import save

        entry = save(db, data.question, data.answer, data.options, data.employer, data.scope)
        background.add_task(service.reconsider_open_questions)  # A question waiting for exactly this answer closes.
        return entry

    @r.delete("/api/knowledge/{entry_id}")
    def knowledge_delete(entry_id: str):
        from ...knowledge import forget

        forget(db, entry_id)
        return {"deleted": True}

    @r.get("/api/knowledge/questionnaires")
    def questionnaires():
        """Voluntary questionnaire modules with their questions, the saved answers and completion. Nothing blocks applying."""
        from ...features.knowledge import questionnaires as q

        return q.overview(db.get_setting("profile", {}) or {})

    @r.put("/api/knowledge/questionnaires/{module}")
    def questionnaire_save(module: str, data: QuestionnaireAnswers, background: BackgroundTasks):
        from ...features.knowledge import questionnaires as q

        try:
            changed = q.save(db, module, data.answers, data.accept_suggestions)
        except KeyError:
            raise HTTPException(404, "Unknown questionnaire") from None
        if changed:
            # A saved fact frees the open question it answers and resumes that application (it used to wait for the
            # next restart).
            background.add_task(service.reconsider_open_questions)
        profile = db.get_setting("profile", {}) or {}
        return {"changed": changed, "module": next(m for m in q.overview(profile)["modules"] if m["id"] == module)}

    return r
