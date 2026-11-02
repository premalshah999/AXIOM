"""Time budgets for tests that drive a real browser.

A test that says "the engine did not wait out its 4 s timeout" has to measure something, and a stopwatch on a busy Mac is a
measurement of the Mac: the same 1.2 s of work took 3 s while production workers, a build and another suite shared its cores
(the browser run of 2026-11-01 failed six such tests that passed alone). The budget below is the limit written in the test,
stretched by how many runnable processes there are per core at that moment (at most 3x), never shrunk. A regression that waits out a whole
timeout is still several times over it. Prefer a count of calls or a virtual clock (support_virtual_time.py) where the code
allows; this is for waits that pass through Chromium.
"""

import os


def load_factor():
    try:
        runnable = os.getloadavg()[0]
    except OSError:
        return 1.0
    return min(3.0, max(1.0, runnable / (os.cpu_count() or 1)))


def scaled(seconds):
    return seconds * load_factor()
