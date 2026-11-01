"""Where one application page's seconds go: a few counters kept in memory and written out as one event per page.

Production logged only the field writes that took 3 s or more, so a Greenhouse run's 62 s between opening the form and the
first read-back had no breakdown. This keeps a running total for each kind of work (scanning the form, waiting for the page to
settle, writing fields, asking the answer provider, reading back, uploading) and for every field written, then `take` returns
them as one small dict. It does no I/O and takes a few microseconds per mark; the event goes through the same buffered writer as
every other event, off the event loop. Labels only, never a value.
"""

import functools
import time
from contextlib import contextmanager

KEEP_FIELDS = 8  # The slowest field writes named in the event; the count and total cover the rest.


class PageClock:
    def __init__(self):
        self.reset()

    def reset(self):
        self.started = time.monotonic()
        self.spans = {}
        self.fields = []

    def add(self, name, seconds):
        entry = self.spans.setdefault(name, [0, 0.0])
        entry[0] += 1
        entry[1] += seconds

    @contextmanager
    def span(self, name):
        started = time.monotonic()
        try:
            yield
        finally:
            self.add(name, time.monotonic() - started)

    def field(self, label, kind, widget, seconds, error=""):
        self.fields.append((str(label)[:60], kind, widget or "", seconds, error))

    def take(self):
        """The page's summary, and a fresh clock. Spans nest (a field write may scan), so they are not additive."""
        wall = time.monotonic() - self.started
        slowest = sorted(self.fields, key=lambda f: -f[3])[:KEEP_FIELDS]
        summary = {
            "wall_s": round(wall, 2),
            "spans": {name: {"n": n, "s": round(s, 2)} for name, (n, s) in sorted(self.spans.items())},
            "fields_n": len(self.fields),
            "fields_s": round(sum(f[3] for f in self.fields), 2),
            "slowest": [
                {"label": label, "type": kind, "widget": widget, "s": round(s, 2)}
                | ({"error": error} if error else {})
                for label, kind, widget, s, error in slowest
            ],
        }
        self.reset()
        return summary


def timed(name):
    """Count an async method's calls and wall time on the instance's `clock`."""

    def wrap(method):
        @functools.wraps(method)
        async def inner(self, *args, **kwargs):
            clock = self.__dict__.get("clock")
            if clock is None:
                return await method(self, *args, **kwargs)
            started = time.monotonic()
            try:
                return await method(self, *args, **kwargs)
            finally:
                clock.add(name, time.monotonic() - started)

        return inner

    return wrap
