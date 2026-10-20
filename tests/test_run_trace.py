import json

from sqlalchemy import select

from jobpilot import pipeline_comparison, service as service_module
from jobpilot.db import Event, Job, Run
from jobpilot.run_trace import phase_timing


def _events(service, run_id, kind=None):
    with service.db.session() as s:
        query = select(Event).where(Event.run_id == run_id).order_by(Event.id)
        if kind:
            query = query.where(Event.kind == kind)
        return list(s.scalars(query))


def test_phase_timing_empty_list_is_empty():
    assert phase_timing([]) == {}


def test_phase_timing_known_timestamps_give_expected_seconds():
    events = [
        ("queued", "2026-10-13T10:00:00+00:00", {}),
        ("browser", "2026-10-13T10:00:05+00:00", {}),
        ("verification", "2026-10-13T10:00:25+00:00", {}),
        (
            "model_call",
            "2026-10-13T10:00:30+00:00",
            {"elapsed_seconds": 2.5, "prompt_tokens": 100, "completion_tokens": 7},
        ),
        ("verification", "2026-10-13T10:00:40+00:00", {}),
        (
            "model_call",
            "2026-10-13T10:00:50+00:00",
            {"elapsed_seconds": 1.5, "prompt_tokens": 50, "completion_tokens": 3},
        ),
        ("email_wait", "2026-10-13T10:01:00+00:00", {}),
        ("email_verified", "2026-10-13T10:01:30+00:00", {}),
        ("notification_error", "2026-10-13T10:01:31+00:00", {}),
        ("finished", "2026-10-13T10:02:00+00:00", {}),
    ]
    timing = phase_timing(events)
    assert timing["segment_s"] == 120.0
    assert timing["queue_s"] == 5.0
    assert timing["open_to_first_verify_s"] == 20.0
    assert timing["model_calls"] == 2
    assert timing["model_s"] == 4.0
    assert timing["prompt_tokens"] == 150
    assert timing["completion_tokens"] == 10
    assert timing["verify_rounds"] == 2
    assert timing["mail_wait_s"] == 30.0
    assert timing["notify_errors"] == 1


def test_phase_timing_omits_segments_without_endpoints():
    timing = phase_timing([("browser", "2026-10-13T10:00:00+00:00", {})])
    assert "segment_s" not in timing
    assert "queue_s" not in timing
    assert "mail_wait_s" not in timing
    assert timing["model_calls"] == 0


def test_completing_a_run_emits_exactly_one_phase_timing(service):
    with service.db.exclusive() as s:
        s.add(Job(id="job", identity="job", url="https://fixture.test/job", status="running"))
        s.add(Run(id="run-timing", job_id="job", state="running", mode="dry_run", packet={}))
    service.db.event("run-timing", "queued", "queued")
    service.db.event("run-timing", "browser", "browser")
    service.db.event("run-timing", "model_call", "model", {"elapsed_seconds": 1.25, "prompt_tokens": 9})

    service.complete("run-timing", {"state": "failed", "reason": "fixture"}, 3.0)

    rows = _events(service, "run-timing", "phase_timing")
    assert len(rows) == 1
    assert rows[0].data["model_calls"] == 1
    assert rows[0].data["prompt_tokens"] == 9
    assert rows[0].data["model_s"] == 1.25
    assert _events(service, "run-timing", "finished")


def test_worker_error_writes_failure_trace_without_message(service, monkeypatch):
    secret = "secret-token-xyz-123"

    class BrokenEngine:
        result = None

        async def run(self, job, run):
            raise TypeError("provider said " + secret)

    with service.db.exclusive() as s:
        s.add(Job(id="job", identity="job", url="https://fixture.test/job", status="new"))
        s.add(Run(id="run-error", job_id="job", state="queued", mode="dry_run", packet={}))
    service.db.event("run-error", "browser", "browser")
    monkeypatch.setattr(pipeline_comparison, "engine_for", lambda *a, **k: BrokenEngine())
    monkeypatch.setattr(pipeline_comparison, "slots", lambda *a, **k: ["owner-1"])
    monkeypatch.setattr(service_module, "may_start", lambda *a, **k: True, raising=False)

    import asyncio

    asyncio.run(service._execute_owned("run-error"))

    traces = _events(service, "run-error", "failure_trace")
    assert len(traces) == 1
    data = traces[0].data
    assert data["exc_class"] == "TypeError"
    assert isinstance(data["locations"], list) and len(data["locations"]) <= 5
    assert data["last_event_kind"] == "browser"
    dumped = json.dumps([traces[0].message, data])
    assert secret not in dumped
    assert _events(service, "run-error", "finished")


def test_notification_errors_are_capped_per_run(service):
    from jobpilot.telegram import NOTIFY_ERROR_CAP, record_notification_error

    service.db.set_setting("telegram_run_pending:run-notify", {"pending": True})
    results = [record_notification_error(service, "run-notify") for _ in range(NOTIFY_ERROR_CAP + 2)]

    assert results == [True] * NOTIFY_ERROR_CAP + [False, False]
    assert len(_events(service, "run-notify", "notification_error")) == NOTIFY_ERROR_CAP
    assert service.db.get_setting("telegram_run_pending:run-notify") == {"pending": False}
