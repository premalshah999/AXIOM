"""Count Playwright protocol round trips.

A picker write is a chain of small renderer calls (evaluate, click, attribute reads). On the shared worker Chrome each
call waits for a busy renderer, so the count of calls, not the Python work, sets how long a write takes under load
(Workday pickers: a plain Yes/No dropdown took 7-9 s at low load in production, 2026-10-19). A test that pins the
count therefore guards speed without depending on the speed of the machine running it.
"""

import contextlib
import os
import traceback
from collections import Counter

from playwright._impl._connection import Connection


class Calls(Counter):
    """Protocol calls by method; `sites` counts calls by the innermost jobpilot source line that issued them."""

    def __init__(self):
        super().__init__()
        self.sites = Counter()

    @property
    def total(self):
        return sum(self.values())


@contextlib.contextmanager
def roundtrips():
    """Yield a Counter of protocol methods sent to the Playwright driver while the block runs."""
    counts = Calls()
    original = Connection._send_message_to_server

    def counting(self, object, method, params, no_reply=False):  # noqa: A002 - mirrors Playwright's signature
        counts[method] += 1
        for frame in reversed(traceback.extract_stack(limit=40)):
            if "/jobpilot/" in frame.filename:
                counts.sites[(method, f"{os.path.basename(frame.filename)}:{frame.lineno}:{frame.name}")] += 1
                break
        return original(self, object, method, params, no_reply)

    Connection._send_message_to_server = counting
    try:
        yield counts
    finally:
        Connection._send_message_to_server = original
