"""Legacy reviewed answers (/api/knowledge). The versioned store is /api/v2/knowledge."""

from fastapi import APIRouter

from ...schemas import KnowledgeInput


def router(service) -> APIRouter:
    db = service.db
    r = APIRouter()

    @r.get("/api/knowledge")
    def knowledge_list(q: str = ""):
        from ...knowledge import search

        return {"entries": search(db, q[:200], limit=500)}

    @r.post("/api/knowledge")
    async def knowledge_add(data: KnowledgeInput):
        from ...knowledge import save

        return save(db, data.question, data.answer, data.options, data.employer, data.scope)

    @r.delete("/api/knowledge/{entry_id}")
    async def knowledge_delete(entry_id: str):
        from ...knowledge import forget

        forget(db, entry_id)
        return {"deleted": True}

    return r
