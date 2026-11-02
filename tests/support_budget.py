"""Time budgets for tests that drive a real browser, and the ceilings the app's own code gives Playwright.

A test that says "the engine did not wait out its 4 s timeout" has to measure something, and a stopwatch on a busy Mac is a
measurement of the Mac: the same 1.2 s of work took 3 s while production workers, a build and another suite shared its cores
(the browser run of 2026-11-01 failed six such tests that passed alone). The budget below is the limit written in the test,
stretched by how busy the machine is at that moment, never shrunk. A regression that waits out a whole timeout is still over it
on an idle machine, where nothing is stretched. Prefer a count of calls or a virtual clock (support_virtual_time.py) where the
code allows; this is for waits that pass through Chromium.

The app's own ceilings on a page read stretch the same way. A read bounded at 1.5 s that runs out comes back empty, so on a busy
Mac a refusal on the screen read as no refusal and the test failed for the machine's sake (test_post_commit_rejection, 2026-11-02).
Tests run at background priority, which on this Mac confines them to a few cores: at 33 runnable processes on 10 cores a new page
took 25 to 50 s, not half a second, so the runnable count per core is multiplied by 3 here. On an idle machine the factor is 1 and
nothing changes.
"""

import os

MOST = 10.0


def load_factor():
    try:
        runnable = os.getloadavg()[0]
    except OSError:
        return 1.0
    return min(MOST, max(1.0, 3 * runnable / (os.cpu_count() or 1)))


def scaled(seconds):
    return seconds * load_factor()


def stretch_playwright_ceilings():
    """Every `timeout=` that reaches Chromium, stretched by load_factor. What a call does and when it succeeds don't change: a ceiling
    is paid only by a call that runs out. A sleep (`wait_for_timeout`) is not a ceiling and is left alone, and 0 stays "no limit"."""
    from playwright._impl import _connection

    if getattr(_connection._augment_params, "_stretched", False):
        return
    plain = _connection._augment_params

    def augment(params, timeout_calculator):
        augmented = plain(params, timeout_calculator)
        if augmented.get("timeout"):
            augmented["timeout"] = augmented["timeout"] * load_factor()
        return augmented

    augment._stretched = True
    _connection._augment_params = augment
