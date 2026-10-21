"""Outbox callbacks cannot complete a superseding durable delivery."""

import asyncio
from types import SimpleNamespace
import time

import pytest
from sqlalchemy import select

from jobpilot.db import Database, Event, Outbox
from jobpilot import outbox

pytestmark = pytest.mark.integration  # a real database, built by hand


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


async def fail_until_dead(db, monkeypatch, kind, run_id="run-dead", attempts_before=0):
    """Fail a delivery on every claim; return how many attempts it took to be parked, and its row."""
    with db.exclusive() as s:
        row = outbox.enqueue(s, kind, "poison", {"run_id": run_id}, run_id=run_id)
        row.attempts = attempts_before
        row_id = row.id

    async def deliver(service, item):
        raise RuntimeError("fixture: the same failure every time")

    monkeypatch.setattr(outbox, "deliver", deliver)
    service = SimpleNamespace(db=db)
    for _ in range(1000):
        with db.exclusive() as s:
            s.get(Outbox, row_id).available_at = 0  # skip the back-off
        if not await outbox.process_one(service):
            break
    with db.session() as s:
        row = s.get(Outbox, row_id)
        return row.attempts, row


@pytest.mark.parametrize("kind, limit", [("notify", 12), ("report", 12), ("repair", 6), ("fact_resume", 288)])
async def test_a_delivery_that_always_fails_is_parked_after_its_limit(tmp_path, monkeypatch, kind, limit):
    """Retries back off to five minutes and never stopped: a poisoned row would be claimed about 288 times a day for
    ever. Parked rows keep their error class, get an event on the run, and are never claimed again."""
    db = Database("sqlite:///" + str(tmp_path / "dead.db"))
    attempts, row = await fail_until_dead(db, monkeypatch, kind)
    assert attempts == limit
    assert row.state == "dead" and row.error == "RuntimeError" and row.finished_at
    assert outbox.claim(db) is None
    with db.session() as s:
        events = list(s.scalars(select(Event).where(Event.run_id == "run-dead", Event.kind == "outbox_dead")))
    assert len(events) == 1 and kind in events[0].message and str(limit) in events[0].message


async def test_a_parked_notification_clears_its_pending_flag(tmp_path, monkeypatch):
    db = Database("sqlite:///" + str(tmp_path / "pending.db"))
    db.set_setting("telegram_run_pending:run-dead", {"pending": True})
    await fail_until_dead(db, monkeypatch, "notify")
    assert db.get_setting("telegram_run_pending:run-dead") == {"pending": False}


async def test_a_delivery_that_recovers_before_its_limit_is_delivered(tmp_path, monkeypatch):
    db = Database("sqlite:///" + str(tmp_path / "recover.db"))
    with db.exclusive() as s:
        row_id = outbox.enqueue(s, "notify", "flaky", {"run_id": "r"}, run_id="r").id
    calls = []

    async def deliver(service, item):
        calls.append(item["attempts"])
        if len(calls) < 5:
            raise RuntimeError("transient")

    monkeypatch.setattr(outbox, "deliver", deliver)
    for _ in range(6):
        with db.exclusive() as s:
            s.get(Outbox, row_id).available_at = 0
        await outbox.process_one(SimpleNamespace(db=db))
    with db.session() as s:
        row = s.get(Outbox, row_id)
        assert row.state == "delivered" and row.attempts == 5 and not row.error


async def test_a_parked_row_is_not_resurrected_by_enqueueing_it_again(tmp_path, monkeypatch):
    db = Database("sqlite:///" + str(tmp_path / "again.db"))
    await fail_until_dead(db, monkeypatch, "repair", attempts_before=0)
    with db.exclusive() as s:
        again = outbox.enqueue(s, "repair", "poison", {"run_id": "run-dead"}, run_id="run-dead")
        assert again.state == "dead"


def test_runtime_health_counts_parked_deliveries_apart(client, service):
    from jobpilot.db import now

    with service.db.exclusive() as s:
        outbox.enqueue(s, "report", "waiting", {}, run_id="a")
        row = outbox.enqueue(s, "report", "parked", {}, run_id="b")
        row.state, row.finished_at = "dead", now()
    body = client.get("/api/runtime-health").json()
    assert body["outbox_pending"] == 1 and body["outbox_dead"] == 1
