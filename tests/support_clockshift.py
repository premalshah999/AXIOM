"""Pytest plugin that moves the wall clock forward, to find tests tied to a date that has since passed.

    CLOCK_SHIFT_DAYS=3 PYTHONPATH=tests PYTEST_WORKERS=4 scripts/dev/pytest-locked.sh -m "not browser" -p support_clockshift

A test that pins a fixed timestamp as "recent" (the 2026-10-27 create flag against a two-hour window, 2026-10-29) passes
on the day it is written and fails later. Every failure under a 3-day and a 20-day shift is such a test: build its
timestamps from datetime.now() instead. `datetime.now`, `datetime.utcnow` and `time.time` move; monotonic clocks do not.
The plugin must load before jobpilot is imported, so name it with -p.
"""

import datetime as _datetime
import os
import time as _time

_offset = float(os.environ.get("CLOCK_SHIFT_DAYS", "0")) * 86400
_real_time = _time.time


class _ShiftedDatetime(_datetime.datetime):
    @classmethod
    def now(cls, tz=None):
        return super().now(tz) + _datetime.timedelta(seconds=_offset)

    @classmethod
    def utcnow(cls):
        return super().utcnow() + _datetime.timedelta(seconds=_offset)


if _offset:
    _datetime.datetime = _ShiftedDatetime
    _time.time = lambda: _real_time() + _offset
