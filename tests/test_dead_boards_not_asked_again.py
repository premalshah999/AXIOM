"""A board that answered 404 is backed off for a day or more (workers.source_failed), but Search now and a new install's first
search asked all of them again: 287 of 680 default boards (clean-install run, 2026-11-01)."""

from datetime import UTC, datetime, timedelta

from jobpilot.db import Source
from jobpilot.jobboard import request_scan


async def test_search_now_skips_boards_in_their_dead_back_off(service, monkeypatch):
    at = datetime.now(UTC)
    with service.db.exclusive() as s:
        for sid, error, days in (("live", "", 0), ("dead", "Source failed: HTTPStatusError: Client error '404 Not Found'", 3), ("flaky", "Source failed: ReadTimeout", 0)):
            s.add(Source(id=sid, name=sid, kind="greenhouse", url=f"https://job-boards.greenhouse.io/{sid}", cron="0 */2 * * *", enabled=True, auto_queue=False,
                         next_run=(at + timedelta(days=days)).isoformat(), error=error))
    service.db.set_setting("job_board_sources", {"ids": ["live", "dead", "flaky"]})
    asked = []

    async def enqueue(sid):
        asked.append(sid)
        return True

    from jobpilot import jobboard

    monkeypatch.setattr(jobboard, "managed_ids", lambda db: {"live", "dead", "flaky"})
    monkeypatch.setattr(jobboard, "setup_sources", lambda db, cfg=None: None)
    await request_scan(service.db, enqueue)
    assert sorted(asked) == ["flaky", "live"]
