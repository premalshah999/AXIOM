"""A clock for tests of deadlines that need no browser: the event loop jumps to its next timer instead of sleeping.

A test of "the wait gave up at its deadline" used to make a hung stand-in sleep 30 s and assert that the call returned in
under 5 s of wall time. On a loaded Mac the return took longer than the margin and the test failed; with the clock below the
elapsed time is exactly the deadline the code under test set, however busy the machine is, and the test takes milliseconds.

    async def test_x():
        with virtual_time():
            started = time.monotonic()
            await thing_that_must_give_up_at_two_seconds()
            assert time.monotonic() - started == pytest.approx(2, abs=0.01)

Only for code that waits on timers: a real socket or browser round trip would see its timeout expire at once. `asyncio.sleep`,
`asyncio.timeout`, `asyncio.wait_for` and `time.monotonic` all follow the virtual clock inside the block.
"""

import asyncio
import contextlib
import time


@contextlib.contextmanager
def virtual_time():
    loop = asyncio.get_running_loop()
    state = {"now": loop.time()}
    real_monotonic = time.monotonic
    selector = loop._selector
    real_select = selector.select

    def now():
        return state["now"]

    def select(timeout=None):
        if timeout is not None and timeout > 0:
            state["now"] += timeout  # nothing is ready: the next timer is due
            timeout = 0
        return real_select(timeout)

    loop.time = now
    selector.select = select
    time.monotonic = now
    try:
        yield state
    finally:
        time.monotonic = real_monotonic
        selector.select = real_select
        del loop.time
