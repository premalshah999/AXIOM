"""Job sources: add, edit, delete, scan now."""

import asyncio

from fastapi import APIRouter, HTTPException

from ...db import Source, record
from ...network import public_url
from ...schemas import SourceInput


def router(service) -> APIRouter:
    db, config = service.db, service.config
    r = APIRouter()

    @r.post("/api/sources")
    async def source_add(data: SourceInput):
        await public_url(data.url, config.allow_private_urls)

        def add():
            with db.session() as s:
                row = Source(**data.model_dump())
                s.add(row)
                s.flush()
                return record(row)

        return await asyncio.to_thread(add)

    @r.put("/api/sources/{source_id}")
    async def source_edit(source_id: str, data: SourceInput):
        await public_url(data.url, config.allow_private_urls)

        def save():
            with db.session() as s:
                row = s.get(Source, source_id)
                if not row:
                    raise HTTPException(404)
                for key, value in data.model_dump().items():
                    setattr(row, key, value)

        await asyncio.to_thread(save)
        return {"saved": True}

    @r.delete("/api/sources/{source_id}")
    def source_delete(source_id: str):
        with db.session() as s:
            row = s.get(Source, source_id)
            if row:
                s.delete(row)
        return {"deleted": True}

    @r.post("/api/sources/{source_id}/discover")
    async def source_discover(source_id: str):

        def exists():
            with db.session() as s:
                return bool(s.get(Source, source_id))

        if not await asyncio.to_thread(exists):
            raise HTTPException(404)
        if not service.dispatch:
            raise ValueError("Workers are disabled")
        from ...workers import enqueue_source

        await enqueue_source(source_id)
        return {"queued": True}

    return r
