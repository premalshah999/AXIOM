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
        answers = iter(["job-1", self.run_state])  # the run's job, then its state: columns only, never the whole row

        def get(_model, key):
            if key.startswith("job_control:") and self.control_state:
                return SimpleNamespace(value={"state": self.control_state})
            return None

        yield SimpleNamespace(get=get, scalar=lambda _statement: next(answers))

    def get_setting(self, key, default=None):
        return {"state": self.control_state} if self.control_state else default


def _engine(armed=False):
    return SimpleNamespace(armed=armed, result=None)


def test_watch_keeps_event_loop_responsive_during_slow_db_reads():
    """The loop must keep running while a read is in flight. Counted, not timed: the read holds until the test has seen the
    loop tick five times (or five seconds pass), so a read on the loop's own thread shows as zero ticks, and a busy machine
    cannot turn a stopwatch margin into a failure."""
    ticks = {"count": 0, "during": []}
    release = threading.Event()

    class HeldDB(SlowDB):
        @contextmanager
        def session(self):
            start = ticks["count"]
            self.threads.append(threading.current_thread() is threading.main_thread())
            release.wait(5)  # released by the test once the loop has ticked; on the loop's own thread nothing ticks meanwhile
            ticks["during"].append(ticks["count"] - start)
            with SlowDB.session(self) as inner:
                yield inner

    async def scenario():
        db = HeldDB("running", None, read_seconds=0)
        execution = asyncio.ensure_future(asyncio.sleep(30))

        async def ticker():
            while True:
                await asyncio.sleep(0.01)
                ticks["count"] += 1

        tick = asyncio.create_task(ticker())
        monitor = asyncio.create_task(watch(SimpleNamespace(db=db), _engine(), execution, "run-1"))
        for _ in range(1000):  # until a read is in flight and the loop has ticked five times since it began
            if db.threads and ticks["count"] >= 5:
                start_count = ticks["count"]
                for _ in range(1000):
                    if ticks["count"] - start_count >= 5:
                        break
                    await asyncio.sleep(0.01)
                break
            await asyncio.sleep(0.01)
        release.set()
        for _ in range(1000):  # and a second read after the pause
            if len(db.threads) >= 2:
                break
            await asyncio.sleep(0.02)
        monitor.cancel()
        tick.cancel()
        execution.cancel()
        await asyncio.gather(monitor, tick, execution, return_exceptions=True)
        return db

    db = asyncio.run(scenario())
    assert len(db.threads) >= 2, "watch() should have polled the database repeatedly"
    assert not any(db.threads), "DB reads must run in a worker thread, not on the event loop"
    assert ticks["during"][0] >= 5, f"the event loop ticked {ticks['during'][0]} times during a read: it was blocked"


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
