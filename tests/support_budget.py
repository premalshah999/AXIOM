"""Time budgets for tests that drive a real browser.

A test that says "the engine did not wait out its 4 s timeout" has to measure something, and a stopwatch on a busy Mac is a
measurement of the Mac: the same 1.2 s of work took 3 s while production workers, a build and another suite shared its cores
(the browser run of 2026-11-01 failed six such tests that passed alone). The budget below is the limit written in the test,
stretched by how busy the machine is at that moment, never shrunk, and it only loosens an assertion: nothing waits for it, so use a
flat ceiling on a wait. A regression that waits out a whole timeout is still over it on an idle machine, where nothing is stretched.
Prefer a count of calls or a virtual clock (support_virtual_time.py) where the code allows; this is for waits that pass through
Chromium.

Tests run at background priority, which on this Mac confines them to a few cores: at 33 runnable processes on 10 cores a new page
took 25 to 50 s, not half a second, so the runnable count per core is multiplied by 3 here.
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


def stretch_playwright_ceilings(monkeypatch):
    """For one test module that only reads a page already there: every `timeout=` that reaches Chromium stretches with the load, so a read
    of a static page bounded at 1.5 s doesn't come back empty on a starved Mac (test_post_commit_rejection, 2026-11-02). Never for all
    tests, and never where a ceiling races something slow on purpose: applied to every test, it let a click bounded at 1.5 s outlast the
    handler holding it, and the tests of the commit guard failed (test_commit_click_undelivered, 2026-11-02)."""
    from playwright._impl import _connection

    plain = _connection._augment_params

    def augment(params, timeout_calculator):
        augmented = plain(params, timeout_calculator)
        if augmented.get("timeout"):
            augmented["timeout"] = augmented["timeout"] * load_factor()
        return augmented

    monkeypatch.setattr(_connection, "_augment_params", augment)
