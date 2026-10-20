"""Outbox callbacks cannot complete a superseding durable delivery."""

import asyncio
from types import SimpleNamespace
import time

import pytest
from sqlalchemy import select

from jobpilot.db import Database, Outbox
from jobpilot import outbox


@pytest.mark.parametrize("outcome", ["ok", "error", "cancel"])
async def test_old_callback_preserves_a_rearmed_successor(tmp_path, monkeypatch, outcome):
    db = Database("sqlite:///" + str(tmp_path / "isolated.db"))
    with db.exclusive() as s:
        row = outbox.enqueue(s, "dispatch", "same-run", {"run_id": "owned"})
        row_id = row.id
    deadline = time.time() + 60

    async def deliver(service, item):
        with db.exclusive() as s:
            row = s.get(Outbox, item["id"])
            row.state, row.lease_until, row.available_at = "pending", 0, deadline
        if outcome == "error":
            raise RuntimeError("fixture")
        if outcome == "cancel":
            raise asyncio.CancelledError()

    monkeypatch.setattr(outbox, "deliver", deliver)
    if outcome == "cancel":
        with pytest.raises(asyncio.CancelledError):
            await outbox.process_one(SimpleNamespace(db=db))
    else:
        await outbox.process_one(SimpleNamespace(db=db))
    with db.session() as s:
        row = s.get(Outbox, row_id)
        assert row.state == "pending" and row.available_at == deadline
        assert not row.finished_at and not row.error
        assert len(list(s.scalars(select(Outbox)))) == 1


async def test_expired_callback_cannot_complete_a_new_claim(tmp_path, monkeypatch):
    db = Database("sqlite:///" + str(tmp_path / "isolated.db"))
    with db.exclusive() as s:
        row = outbox.enqueue(s, "dispatch", "same-run", {})
        row_id = row.id

    async def deliver(service, item):
        with db.exclusive() as s:
            s.get(Outbox, item["id"]).lease_until = time.time() - 1
        replacement = outbox.claim(db)
        assert replacement["attempts"] == item["attempts"] + 1

    monkeypatch.setattr(outbox, "deliver", deliver)
    await outbox.process_one(SimpleNamespace(db=db))
    with db.session() as s:
        row = s.get(Outbox, row_id)
        assert row.state == "processing" and row.attempts == 2
        assert not row.finished_at
