"""After downtime (a sleeping laptop, a hang, a restart) DBOS recovers every pending scheduled tick at once. Each one
took the global lock and a pooled connection, and the app exhausted its connection pool at every start (2026-10-20)."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from jobpilot.db import POOL_OVERFLOW, POOL_SIZE, engine_options
from jobpilot.workers import stale_tick


def test_a_recovered_tick_does_nothing_and_a_live_tick_runs():
    now = datetime(2026, 10, 20, 13, 0, tzinfo=UTC)
    assert stale_tick(now - timedelta(hours=9), now)
    assert stale_tick(now - timedelta(minutes=6), now)
    assert not stale_tick(now - timedelta(seconds=30), now)
    # The schedule's own time zone and a naive (UTC) time compare correctly.
    eastern = (now - timedelta(seconds=30)).astimezone(ZoneInfo("America/New_York"))
    assert not stale_tick(eastern, now)
    assert stale_tick(datetime(2026, 10, 20, 4, 0), now)


def test_the_pool_fits_eight_workers_and_the_background_queues():
    options = engine_options("postgresql+psycopg://u:p@db:5432/x")
    assert options["pool_size"] == POOL_SIZE >= 10 and options["max_overflow"] == POOL_OVERFLOW >= 20
    assert "pool_size" not in engine_options("sqlite:///x.db")
