"""Readiness waits that ran to their whole deadline for no reason (production events, 2026-10-26..28)."""

import time

from jobpilot.forms import FormSession, NetworkMonitor


async def test_a_frame_replaced_during_the_wait_is_read_again_not_retried_until_the_deadline():
    class Detached:
        async def evaluate(self, *args):
            raise RuntimeError("Frame was detached")

    class Live:
        async def evaluate(self, *args):
            return False  # quiet

    reads = []

    async def frames():
        reads.append(1)
        return [Detached()] if len(reads) == 1 else [Live()]

    form = object.__new__(FormSession)
    form.application_frames = frames
    form.network = None
    started = time.monotonic()
    assert await form.settle(timeout=8)
    assert time.monotonic() - started < 6, "the wait kept calling the detached frame"
    assert len(reads) == 2


def test_linkedin_and_pixel_beacons_do_not_hold_readiness_but_employer_calls_do():
    class Target:
        def on(self, *args):
            pass

    class Request:
        resource_type = "fetch"

        def __init__(self, url):
            self.url = url

    monitor = NetworkMonitor(Target())
    monitor.last = 0
    for beacon in (
        "https://www.linkedin.com/talentwidgets/apply-with-linkedin",
        "https://www.linkedin.com/li/track",
        "https://c.talentplatform.eu/com.snowplowanalytics.snowplow/tp2",
        "https://mon.tiktokv.com/monitor_browser/collect/batch/",
        "https://www.google.com/measurement/conversion",
    ):
        monitor._start(Request(beacon))
        assert monitor.quiet(0), beacon
    for employer in (
        "https://tenant.wd5.myworkdayjobs.com/wday/cxs/t/site/applications",
        "https://www.linkedin.com/voyager/api/anything-else",
        "https://tenant.fa.oraclecloud.com/hcmRestApi/resources/latest/x",
    ):
        request = Request(employer)
        monitor._start(request)
        assert not monitor.quiet(0), employer
        monitor._end(request)
