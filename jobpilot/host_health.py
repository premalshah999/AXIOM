"""How much work the worker host can take right now.

Three conditions end in the same number, the applications allowed to run at once. `provider_health.dispatch_limit`
(admission: the autopilot, retries, the owner's Apply) and `Service._execute_owned` (the claim of a browser slot)
both ask for it, so a gate cannot be bypassed by a run that was queued earlier.

* **The event loop runs late.** One Python loop drives every browser, so when it is late every Playwright round trip
  is late and field writes miss their 12 s deadline. From 2026-10-18 19:00 to 10-19 06:00 the host was overloaded (a
  test suite, a Mac on battery): 940 writes missed the deadline, 70 runs timed out and a confirmed run took 250 s
  instead of 115 s, while the scheduler kept starting work and every timed-out run spent one of its job's three
  starts. `HostLoad` samples how late a 0.5 s timer fires; the 95th percentile over 30 s picks the limit, and
  `host_overloaded_result` makes a deadline timeout inside such a window a free retry.
* **The worker Chrome is unreachable.** 375 runs were dispatched into one dead browser between 2026-10-11 21:00 and
  10-12 02:00. After three runs in a row end without opening a page, no start is allowed for a minute, then two,
  four, up to fifteen; the first start after the pause is the probe that decides (`note_browser`).
* **The server is stopping** (a deploy). Nothing new starts and no application may begin its commit
  (`ServerDraining`); `Service.drain` waits for the commits already in flight.

Owner switch, no redeploy: the `host_load` setting `{"enabled": false}` turns the load gate and the free-timeout rule
off; `loaded_p95` and `overloaded_p95` (seconds) retune them. The monitor mirrors its state to `host_load_state`.
"""

import asyncio
import math
import threading
import time
from collections import deque
from datetime import UTC, datetime, timedelta
from typing import NamedTuple

from .application_budget import BROWSER_OUTAGE, HOST_OVERLOADED, NOTHING_SENT_STATES
from .db import Setting

SAMPLE_SECONDS = 0.5
WINDOW_SECONDS = 30.0
# About ten seconds of samples before the percentile means anything; a single long stall counts at once.
MIN_SAMPLES = 20
# Seconds of lateness at the 95th percentile. A 12 s field write makes about ten round trips, so a loop that is
# regularly a second late cannot finish one; 0.4 s is the first sign.
LOADED_P95 = 0.4
OVERLOADED_P95 = 1.0
SEVERE_SECONDS = 5.0
# A monitor that stopped reporting says nothing about the host.
STALE_SECONDS = 10.0
HISTORY_SECONDS = 6 * 3600
# A timeout is blamed on the host when it was overloaded for this long or this share of the run, whichever is larger.
MIN_OVERLOAD_SECONDS = 30.0
OVERLOAD_SHARE = 0.25
SETTING = "host_load"
STATE_SETTING = "host_load_state"
SAVE_SECONDS = 10.0

OUTAGE_KEY = "browser_outage"
OUTAGE_AFTER = 3
OUTAGE_BASE_SECONDS = 60
OUTAGE_MAX_SECONDS = 900
# Only runs that opened the employer's page say the browser works; the owner's pause or cancel says nothing.
OUTAGE_NEUTRAL_STATES = {"paused", "cancelled", "skipped", "queued", "running", "submitting"}
DEADLINE_CAUSES = {"operation_timeout", "control_timeout"}

INTERRUPTED = "Worker interrupted before submission; draft recovery scheduled"


class Gate(NamedTuple):
    """How many applications may run at once, and why that is fewer than the workers (code '' when nothing limits)."""

    limit: int
    code: str = ""
    reason: str = ""


class ServerDraining(ValueError):
    """A commit was refused because the server is stopping. Nothing was sent; the draft resumes after the restart."""

    def __init__(self):
        super().__init__(INTERRUPTED)


def refused_commit_result(exc):
    """The run result for a refused `reserve_submission`. A stop is free; any other refusal keeps its old meaning."""
    if isinstance(exc, ServerDraining):
        return {"state": "waiting_browser", "reason": str(exc), "recovery_cause": "worker_interrupted"}
    return {"state": "waiting_browser", "reason": str(exc), "recovery_cause": "control_observation"}


class HostLoad:
    """A rolling window of event-loop lateness and the periods in which the host counted as overloaded."""

    def __init__(self, *, wall=time.time):
        self.wall = wall
        self.lock = threading.Lock()
        self.samples = deque()  # (wall time, seconds late)
        self.intervals = []  # [start, end or None] of overloaded periods, wall time
        self.state = "unknown"
        self.config = {"enabled": True, "loaded_p95": LOADED_P95, "overloaded_p95": OVERLOADED_P95}

    def configure(self, value):
        """Apply the owner's `host_load` setting; anything unusable keeps the default."""
        value = value if isinstance(value, dict) else {}
        config = {"enabled": value.get("enabled", True) is not False}
        for key, default in (("loaded_p95", LOADED_P95), ("overloaded_p95", OVERLOADED_P95)):
            number = value.get(key)
            config[key] = float(number) if isinstance(number, (int, float)) and 0.05 <= number <= 30 else default
        if config["overloaded_p95"] < config["loaded_p95"]:
            config["loaded_p95"], config["overloaded_p95"] = LOADED_P95, OVERLOADED_P95
        with self.lock:
            self.config = config
            self._apply(self._classify(), self.samples[-1][0] if self.samples else self.wall())

    def record(self, lag, at=None):
        """Add one sample (seconds a timer fired late) and return the state it leaves the host in."""
        at = self.wall() if at is None else at
        with self.lock:
            self.samples.append((at, max(0.0, float(lag))))
            while self.samples and self.samples[0][0] < at - WINDOW_SECONDS:
                self.samples.popleft()
            state = self._classify()
            # One long stall (a Mac that slept, a frozen loop) overloaded the host for the whole stall, not just from
            # the moment it ended: the runs that timed out meanwhile are the ones to excuse.
            self._apply(state, at, since=at - lag if lag >= SEVERE_SECONDS else None)
        return state

    def _apply(self, state, at, since=None):
        """Move to `state`, opening or closing an overloaded period. Call with the lock held."""
        if state == "overloaded" and self.state != "overloaded":
            self.intervals.append([at if since is None else since, None])
        elif state != "overloaded" and self.state == "overloaded" and self.intervals and self.intervals[-1][1] is None:
            self.intervals[-1][1] = at
        self.state = state
        horizon = at - HISTORY_SECONDS
        self.intervals = [i for i in self.intervals if i[1] is None or i[1] >= horizon]

    def _percentile(self, lags, share):
        return lags[max(0, math.ceil(share * len(lags)) - 1)]

    def _classify(self):
        lags = sorted(lag for _, lag in self.samples)
        if not lags:
            return "unknown"
        if lags[-1] >= SEVERE_SECONDS:
            return "overloaded"
        if len(lags) < MIN_SAMPLES:
            return "unknown"
        p95 = self._percentile(lags, 0.95)
        if p95 >= self.config["overloaded_p95"]:
            return "overloaded"
        return "loaded" if p95 >= self.config["loaded_p95"] else "ok"

    def current(self, at=None):
        """ok, loaded, overloaded, or unknown (no monitor, or it stopped reporting). A disabled gate is always ok."""
        at = self.wall() if at is None else at
        with self.lock:
            if not self.config["enabled"]:
                return "ok"
            if not self.samples or at - self.samples[-1][0] > STALE_SECONDS:
                return "unknown"
            return self.state

    def limit(self, workers, at=None):
        state = self.current(at)
        if state == "overloaded":
            return min(1, workers)
        if state == "loaded":
            return max(1, workers // 2)
        return workers

    def overloaded_seconds(self, start, end=None):
        """Seconds of [start, end] (wall clock) in which the host counted as overloaded."""
        end = self.wall() if end is None else end
        total = 0.0
        with self.lock:
            for began, ended in self.intervals:
                total += max(0.0, min(end, end if ended is None else ended) - max(start, began))
        return total

    def snapshot(self, at=None):
        at = self.wall() if at is None else at
        with self.lock:
            lags = sorted(lag for _, lag in self.samples)
            fresh = bool(self.samples) and at - self.samples[-1][0] <= STALE_SECONDS
            state = "unknown" if not fresh else self.state if self.config["enabled"] else "ok"
            return {
                "state": state,
                "enabled": self.config["enabled"],
                "p95_seconds": round(self._percentile(lags, 0.95), 3) if lags else None,
                "max_seconds": round(lags[-1], 3) if lags else None,
                "samples": len(lags),
                "overloaded_periods": len(self.intervals),
            }


monitor = HostLoad()
DRAINING = threading.Event()


def begin_drain():
    DRAINING.set()


def draining():
    return DRAINING.is_set()


def _read(key, db=None, session=None):
    if session is not None:
        row = session.get(Setting, key)
        return dict(row.value or {}) if row else {}
    if db is not None:
        return dict(db.get_setting(key, {}) or {})
    return {}


def _moment(value):
    moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment


def check(workers, *, db=None, session=None, load=None, at=None):
    """The tightest gate on new work right now. `db` or an open `session` reads the browser circuit."""
    if draining():
        return Gate(0, "draining", "The server is restarting; applications start again afterwards")
    now = at or datetime.now(UTC)
    gates = []
    outage = _read(OUTAGE_KEY, db, session)
    failures = int(outage.get("failures") or 0)
    if failures >= OUTAGE_AFTER and outage.get("until"):
        if now < _moment(outage["until"]):
            return Gate(
                0,
                "browser_unavailable",
                f"The worker Chrome opened none of the last {failures} applications; starts resume shortly",
            )
        gates.append(Gate(min(1, workers), "browser_probe", "One application tests the worker Chrome before the rest start"))
    load = load or monitor
    stamp = now.timestamp() if at else None
    state = load.current(stamp)
    if state in {"loaded", "overloaded"}:
        gates.append(
            Gate(
                load.limit(workers, stamp),
                "host_overloaded" if state == "overloaded" else "host_loaded",
                "The worker host is running late; fewer applications run at once until it recovers",
            )
        )
    return min(gates, key=lambda g: g.limit) if gates else Gate(workers)


def concurrency_limit(workers, **kwargs):
    return check(workers, **kwargs).limit


def is_browser_outage(state, reason):
    return state in NOTHING_SENT_STATES and (reason or "").startswith(BROWSER_OUTAGE)


def note_browser(session, *, state, reason, started_at, at=None):
    """Record a finished run for the browser circuit, inside the caller's transaction.

    A run that could not open a page adds a failure; from the third in a row starts pause for a minute, doubling to
    fifteen. A run that started after the last failure and reached the employer's page proves the browser works and
    closes the circuit. A run that started earlier, or ended by the owner's hand, proves nothing.
    """
    at = at or datetime.now(UTC)
    row = session.get(Setting, OUTAGE_KEY)
    value = dict(row.value or {}) if row else {}
    if is_browser_outage(state, reason):
        failures = int(value.get("failures") or 0) + 1
        value = {"failures": failures, "last_at": at.isoformat()}
        if failures >= OUTAGE_AFTER:
            seconds = min(OUTAGE_MAX_SECONDS, OUTAGE_BASE_SECONDS * 2 ** (failures - OUTAGE_AFTER))
            value["until"] = (at + timedelta(seconds=seconds)).isoformat()
    else:
        if not value.get("failures") or state in OUTAGE_NEUTRAL_STATES:
            return
        last = value.get("last_at")
        if started_at and last and _moment(started_at) <= _moment(last):
            return
        value = {"failures": 0, "cleared_at": at.isoformat()}
    if row:
        row.value = value
    else:
        session.add(Setting(key=OUTAGE_KEY, value=value))


def host_overloaded_result(result, *, run_state, started_at, ended_at=None, load=None):
    """`result` rewritten as a free retry when the host's overload explains a deadline timeout, else None.

    Only a run that was still pre-commit (`running`) and ended on a clock (an operation or control timeout) qualifies:
    a commit in flight stays protected, and a stall on the page is not the host's fault.
    """
    load = load or monitor
    if (
        run_state != "running"
        or not started_at
        or result.get("state") not in NOTHING_SENT_STATES
        or result.get("recovery_cause") not in DEADLINE_CAUSES
        or not load.config["enabled"]
    ):
        return None
    begin = _moment(started_at).timestamp()
    end = ended_at if ended_at is not None else load.wall()
    seconds = load.overloaded_seconds(begin, end)
    if seconds < max(MIN_OVERLOAD_SECONDS, OVERLOAD_SHARE * max(1.0, end - begin)):
        return None
    return {
        **result,
        "reason": HOST_OVERLOADED + " and the application ran out of time before anything was sent; "
        "it is retried when the host recovers",
        "recovery_cause": "host_overloaded",
        "blocker_type": "site_transient",
        "diagnostics": {
            **(result.get("diagnostics") if isinstance(result.get("diagnostics"), dict) else {}),
            "original_reason": str(result.get("reason", ""))[:200],
            "overloaded_seconds": round(seconds),
        },
    }


def snapshot(db=None):
    """For /api/runtime-health: the monitor, the browser circuit and whether a drain has begun."""
    outage = _read(OUTAGE_KEY, db)
    return {
        **monitor.snapshot(),
        "draining": draining(),
        "browser_failures_in_a_row": int(outage.get("failures") or 0),
        "browser_paused_until": outage.get("until") if int(outage.get("failures") or 0) >= OUTAGE_AFTER else None,
    }


async def watch(db=None, *, interval=SAMPLE_SECONDS, load=None):
    """Sample the event loop for as long as the server runs. Never raises; cancel it to stop."""
    load = load or monitor
    expected = time.monotonic() + interval
    saved = 0.0
    while True:
        try:
            await asyncio.sleep(interval)
            arrived = time.monotonic()
            load.record(arrived - expected)
            expected = arrived + interval
            if db is not None and arrived - saved >= SAVE_SECONDS:
                saved = arrived
                await asyncio.to_thread(_mirror, db, load)
        except asyncio.CancelledError:
            raise
        except Exception:
            expected = time.monotonic() + interval  # a failed mirror must not end the monitor or inflate the next lag


def _mirror(db, load):
    """Read the owner's switch and publish the monitor's state; both are small single-row writes."""
    load.configure(db.get_setting(SETTING, {}))
    db.set_setting(STATE_SETTING, {**load.snapshot(), "at": datetime.now(UTC).isoformat()})
