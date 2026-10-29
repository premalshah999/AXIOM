"""A board that answers not-found or refuses the public API stops costing scans (2026-10-29).

450 of 4,263 sources failed every scan (Greenhouse/Ashby 404, career pages 404, Workday 422), most with 34+ failures in
a row, and each was retried every 24 hours for good."""

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from jobpilot.db import Source
from jobpilot.features.discovery.policy import dead_source_wait, permanent_failure


def status_error(code):
    request = httpx.Request("GET", "https://boards-api.greenhouse.io/v1/boards/x/jobs")
    return httpx.HTTPStatusError("boom", request=request, response=httpx.Response(code, request=request))


@pytest.mark.parametrize("code,permanent", [(404, True), (410, True), (403, True), (422, True), (429, False),
                                            (500, False), (502, False), (503, False)])
def test_only_gone_or_refused_boards_are_permanent(code, permanent):
    assert permanent_failure(status_error(code)) is permanent


def test_resolution_and_certificate_failures_are_permanent_timeouts_are_not():
    assert permanent_failure(ValueError("The URL hostname could not be resolved"))
    assert permanent_failure(RuntimeError("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed"))
    assert not permanent_failure(httpx.ConnectTimeout("slow"))


def test_the_wait_grows_from_a_day_to_two_weeks():
    assert [dead_source_wait(n) for n in (1, 2, 3, 4, 5, 35)] == [
        timedelta(days=1), timedelta(days=2), timedelta(days=4), timedelta(days=8), timedelta(days=14), timedelta(days=14)]


def test_a_dead_source_is_deferred_by_its_failures_and_a_flaky_one_for_a_day(service):
    from jobpilot.workers import source_failed

    with service.db.exclusive() as s:
        dead = Source(name="Gone", kind="greenhouse", url="https://job-boards.greenhouse.io/gone", cron="0 */2 * * *")
        flaky = Source(name="Slow", kind="greenhouse", url="https://job-boards.greenhouse.io/slow", cron="0 */2 * * *")
        s.add_all([dead, flaky])
        s.flush()
        ids = [dead.id, flaky.id]
    errors = {ids[0]: status_error(404), ids[1]: status_error(503)}
    for _ in range(3):
        for source_id in ids:
            source_failed(service.db, source_id, errors[source_id], True, "America/New_York")
    with service.db.session() as s:
        waits = {sid: datetime.fromisoformat(s.get(Source, sid).next_run) - datetime.now(UTC) for sid in ids}
        assert s.get(Source, ids[0]).error.startswith("Source failed: HTTPStatusError")
    # Third consecutive 404 waits four days; a 503 keeps the daily retry.
    assert timedelta(days=3, hours=23) < waits[ids[0]] <= timedelta(days=4)
    assert timedelta(hours=23) < waits[ids[1]] <= timedelta(days=1)
