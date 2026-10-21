"""FormSession.settle waits in the page, not with a protocol call every 0.1 s.

The readiness check used to evaluate a script in every application frame, sleep 0.1 s and repeat until two quiet
reads in a row. Settle is the largest explicit wait in an application (Workday 2,324 slow waits and about 9,500 s in
fourteen days of confirmed runs, Oracle and Eightfold about 5 s per wait) and on the shared worker Chrome each call waits
for a busy renderer, so a long settle multiplied the cost of the wait itself. The wait now holds inside one call
per frame and the transport check stays in Python, where it costs no protocol call.

These tests pin the call count, which does not depend on the speed of the machine, and the behaviour it must keep.
"""

import time
from types import SimpleNamespace

from jobpilot.forms import FormSession
from support_roundtrips import roundtrips


def form_for(page, network=None):
    form = object.__new__(FormSession)
    form.network = network

    async def frames():
        return [page.main_frame]

    form.application_frames = frames
    form.emit = lambda *args: None
    return form


BURST = """<form><label>Name<input id=name></label></form><script>
  const end = Date.now() + 1200;
  const timer = setInterval(() => { document.getElementById('name').setAttribute('aria-invalid', Date.now() % 2 ? 'true' : 'false');
    if (Date.now() > end) clearInterval(timer) }, 25);
</script>"""


async def test_a_one_second_wait_costs_a_handful_of_calls_not_one_per_poll(page):
    await page.set_content(BURST)
    with roundtrips() as calls:
        started = time.monotonic()
        assert await form_for(page).settle(timeout=5, quiet=0.2)
        elapsed = time.monotonic() - started
    assert elapsed >= 1.0, "settled while the form was still changing"
    assert calls.total <= 3, dict(calls)


async def test_a_page_that_never_settles_still_times_out_with_few_calls(page):
    await page.set_content(
        "<form><input id=name></form><script>setInterval(() => document.getElementById('name').setAttribute('aria-invalid', Math.random()), 20)</script>"
    )
    with roundtrips() as calls:
        started = time.monotonic()
        assert not await form_for(page).settle(timeout=0.8, quiet=0.2)
        assert time.monotonic() - started < 1.5
    assert calls.total <= 3, dict(calls)


async def test_requests_in_flight_hold_readiness_without_any_protocol_call(page):
    await page.set_content("<form><input id=name></form>")
    flag = {"quiet": False}
    network = SimpleNamespace(quiet=lambda period: flag["quiet"])
    form = form_for(page, network)
    await form.application_frames()  # warm the page so only the wait is measured
    await form.settle(timeout=0.4, quiet=0.1)
    with roundtrips() as calls:
        started = time.monotonic()

        async def finish():
            import asyncio

            await asyncio.sleep(0.6)
            flag["quiet"] = True

        import asyncio

        task = asyncio.create_task(finish())
        assert await form.settle(timeout=3, quiet=0.1)
        await task
        assert time.monotonic() - started >= 0.6
    assert calls.total <= 2, dict(calls)


async def test_a_quiet_page_settles_in_one_call(page):
    await page.set_content("<form><label>Name<input></label></form>")
    form = form_for(page)
    with roundtrips() as calls:
        assert await form.settle(timeout=3, quiet=0.15)
    assert calls.total == 1, dict(calls)


async def test_a_navigation_during_the_wait_is_survived(page):
    await page.set_content("<form><input id=name></form>")
    form = form_for(page)
    await page.evaluate("setTimeout(() => location.href = 'data:text/html,<form><input></form>', 150)")
    assert await form.settle(timeout=5, quiet=0.2)


async def test_a_wait_that_hits_its_deadline_says_what_held_it(page):
    await page.set_content("<form><input id=name></form>")
    events = []
    network = SimpleNamespace(quiet=lambda period: False, pending=lambda: ["POST acme.example/wday/save"])
    form = form_for(page, network)
    form.emit = lambda kind, message, data=None: events.append((kind, data))
    assert not await form.settle(timeout=1.1, quiet=0.1)
    (kind, data), = events
    assert kind == "settle_timing"
    assert data["held_by"] == "network" and data["requests"] == ["POST acme.example/wday/save"]

    events.clear()
    await page.set_content("<form aria-busy=true><input id=name></form>")
    form = form_for(page)
    form.emit = lambda kind, message, data=None: events.append((kind, data))
    assert not await form.settle(timeout=1.1, quiet=0.1)
    assert events[0][1]["held_by"] == "page"
