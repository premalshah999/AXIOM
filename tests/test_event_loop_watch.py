"""watch() reads the DB off the shared event loop and keeps the owner interrupt semantics."""

import asyncio
import threading
import time
from contextlib import contextmanager
from types import SimpleNamespace

from jobpilot.job_control import watch


class SlowDB:
    """Synchronous reads sleep like a busy Postgres connection would."""

    def __init__(self, run_state, control_state, read_seconds=0.2):
        self.run_state, self.control_state, self.read_seconds = run_state, control_state, read_seconds
        self.threads = []

    @contextmanager
    def session(self):
        self.threads.append(threading.current_thread() is threading.main_thread())
        time.sleep(self.read_seconds)
        yield SimpleNamespace(get=lambda model, key: SimpleNamespace(state=self.run_state, job_id="job-1"))

    def get_setting(self, key, default=None):
        return {"state": self.control_state} if self.control_state else default


def _engine(armed=False):
    return SimpleNamespace(armed=armed, result=None)


def test_watch_keeps_event_loop_responsive_during_slow_db_reads():
    async def scenario():
        db = SlowDB("running", None)
        execution = asyncio.ensure_future(asyncio.sleep(30))
        gaps = []

        async def ticker():
            last = time.monotonic()
            while True:
                await asyncio.sleep(0.01)
                now = time.monotonic()
                gaps.append(now - last)
                last = now

        tick = asyncio.create_task(ticker())
        monitor = asyncio.create_task(watch(SimpleNamespace(db=db), _engine(), execution, "run-1"))
        await asyncio.sleep(1.5)
        monitor.cancel()
        tick.cancel()
        execution.cancel()
        await asyncio.gather(monitor, tick, execution, return_exceptions=True)
        return db, gaps

    db, gaps = asyncio.run(scenario())
    assert len(db.threads) >= 2, "watch() should have polled the database repeatedly"
    assert not any(db.threads), "DB reads must run in a worker thread, not on the event loop"
    assert max(gaps) < 0.1, f"event loop stalled for {max(gaps):.3f}s during a DB read"


def test_owner_pause_interrupts_unarmed_run_and_releases_writer():
    async def scenario():
        db = SlowDB("running", "paused", read_seconds=0)
        engine = _engine(armed=False)
        execution = asyncio.ensure_future(asyncio.sleep(30))
        await asyncio.wait_for(watch(SimpleNamespace(db=db), engine, execution, "run-1"), 3)
        return engine, execution

    engine, execution = asyncio.run(scenario())
    assert engine.result["state"] == "paused"
    assert engine.result["writer_released"] is True
    assert execution.cancelled()


def test_armed_run_is_never_interrupted_by_owner_pause():
    async def scenario():
        db = SlowDB("running", "deleted", read_seconds=0)
        engine = _engine(armed=True)
        execution = asyncio.ensure_future(asyncio.sleep(30))
        monitor = asyncio.create_task(watch(SimpleNamespace(db=db), engine, execution, "run-1"))
        await asyncio.sleep(1.2)
        still_watching = not monitor.done()
        interrupted = execution.cancelled()
        monitor.cancel()
        execution.cancel()
        await asyncio.gather(monitor, execution, return_exceptions=True)
        return engine, interrupted, still_watching

    engine, interrupted, still_watching = asyncio.run(scenario())
    assert engine.result is None
    assert not interrupted
    assert still_watching
