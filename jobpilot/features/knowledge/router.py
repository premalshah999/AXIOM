"""/api/v2/knowledge — mounted inside the app, so the existing session
authentication and JSON/multipart mutation checks apply unchanged."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from . import attestations, migration, store
from .models import SaveAnswer, VersionConflict
from .tables import KnAlias, KnEmployer, KnFact


class RetireInput(BaseModel):
    expected_version: int | None = None


def router(service) -> APIRouter:
    db = service.db
    r = APIRouter(prefix="/api/v2/knowledge", tags=["knowledge"])

    @r.get("/facts")
    def facts(
        category: str | None = None,
        layer: str | None = None,
        scope: str | None = None,
        employer_id: str | None = None,
        q: str = "",
        page: int = 1,
        size: int = 25,
    ):
        migration.sync(db)
        return store.list_facts(
            db,
            category=category,
            layer=layer,
            scope=scope,
            employer_id=employer_id,
            q=q,
            page=page,
            size=size,
        )

    @r.post("/facts")
    async def save(data: SaveAnswer):
        try:
            return store.save_confirmed_answer(db, data)
        except VersionConflict as exc:
            raise HTTPException(409, str(exc)) from None

    class AttestInput(BaseModel):
        statement: str = Field(min_length=1, max_length=1000)

    @r.get("/attestations")
    def attestations_list():
        return {
            "items": [
                {**spec, "confirmed": attestations.attestation(db, key)}
                for key, spec in attestations.ATTESTATIONS.items()
            ]
        }

    @r.post("/attestations/{attestation_id}")
    async def attest(attestation_id: str, data: AttestInput):
        try:
            attestations.attest(db, attestation_id, data.statement)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        return {"confirmed": attestations.attestation(db, attestation_id)}

    @r.post("/facts/{fact_id}/retire")
    async def retire(fact_id: str, data: RetireInput):
        try:
            return {"store_version": store.retire_fact(db, fact_id, data.expected_version)}
        except VersionConflict as exc:
            raise HTTPException(409, str(exc)) from None

    @r.get("/summary")
    def summary():
        migration.sync(db)
        with db.session() as s:
            live = store.readable(
                select(KnFact.category, KnFact.layer, func.count()).group_by(KnFact.category, KnFact.layer)
            )
            counts = {}
            for category, layer, n in s.execute(live):
                counts.setdefault(category, {})[layer] = n
            employers = s.scalar(select(func.count()).select_from(KnEmployer))
            return {"categories": counts, "employers": employers, "store_version": store.store_version(s)}

    @r.get("/employers")
    def employer_list(q: str = "", page: int = 1, size: int = 25):
        size, page = max(1, min(100, size)), max(1, page)
        with db.session() as s:
            query = select(KnEmployer)
            if q:
                query = query.where(KnEmployer.display.ilike(f"%{q[:120]}%"))
            total = s.scalar(select(func.count()).select_from(query.subquery()))
            rows = list(s.scalars(query.order_by(KnEmployer.display).offset((page - 1) * size).limit(size)))
            memory = dict(
                s.execute(
                    store.readable(
                        select(KnFact.employer_id, func.count()).where(
                            KnFact.employer_id.in_([e.id for e in rows])
                        )
                    ).group_by(KnFact.employer_id)
                ).all()
            )
            aliases = {}
            for a in s.scalars(select(KnAlias).where(KnAlias.employer_id.in_([e.id for e in rows]))):
                aliases.setdefault(a.employer_id, []).append({"alias": a.alias, "kind": a.kind})
            return {
                "items": [
                    {
                        "id": e.id,
                        "display": e.display,
                        "aliases": aliases.get(e.id, []),
                        "memory": memory.get(e.id, 0),
                    }
                    for e in rows
                ],
                "total": total,
                "page": page,
                "size": size,
            }

    return r
