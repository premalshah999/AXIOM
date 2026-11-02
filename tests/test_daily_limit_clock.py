"""The daily limit counts a day, in the person's time zone, not everything since the batch started."""
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from jobpilot import autopilot
from jobpilot.db import Job, Run
from test_autopilot_dispatch import add_job, run_tick


def seed_confirmed(service, key, created_at):
    with service.db.exclusive() as s:
        job = Job(identity="done-" + key, url=f"https://{key}.example.test/job/1", ats="oracle", title="Engineer",
                  company="Example", status="confirmed", demo=False)
        s.add(job)
        s.flush()
        s.add(Run(job_id=job.id, state="confirmed", mode="submit", created_at=created_at, receipt={}))
        return job.id


def seed_batch(service, started):
    service.db.set_setting("job_board_autopilot", {
        "state": "active", "target": service.config.daily_application_limit, "started_at": started,
        "run_ids": [], "considered_ids": [], "confirmed": 0, "queued": 0, "skipped": [], "last_tick": None,
    })


async def idle_queue(job_id, *args, **kwargs):
    return {"id": "run-" + job_id}


async def test_twenty_yesterday_and_five_today_is_five_toward_the_daily_limit(service, monkeypatch):
    service.config.daily_application_limit = 25
    zone = ZoneInfo(service.config.timezone)
    local_now = datetime.now(zone)
    start_of_today = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    yesterday = (start_of_today - timedelta(hours=2)).astimezone(UTC).isoformat()
    today = (start_of_today + timedelta(seconds=1)).astimezone(UTC).isoformat()
    for n in range(20):
        seed_confirmed(service, f"y{n}", yesterday)
    for n in range(5):
        seed_confirmed(service, f"t{n}", today)
    seed_batch(service, (start_of_today - timedelta(days=3)).astimezone(UTC).isoformat())
    batch = await run_tick(service, monkeypatch, idle_queue)
    assert batch["confirmed"] == 5
    assert batch["state"] == "active"


async def test_twenty_five_today_completes_the_batch_and_says_the_day(service, monkeypatch):
    service.config.daily_application_limit = 25
    zone = ZoneInfo(service.config.timezone)
    start_of_today = datetime.now(zone).replace(hour=0, minute=0, second=0, microsecond=0)
    today = (start_of_today + timedelta(seconds=1)).astimezone(UTC).isoformat()
    for n in range(25):
        seed_confirmed(service, f"t{n}", today)
    seed_batch(service, (start_of_today - timedelta(days=3)).astimezone(UTC).isoformat())
    batch = await run_tick(service, monkeypatch, idle_queue)
    assert batch["state"] == "complete"
    assert batch["finished_day"] == datetime.now(zone).date().isoformat()
