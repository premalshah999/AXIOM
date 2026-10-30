"""Give freed heap back to the operating system, and say in the log what the process holds.

A heap census of production (read from /proc/<pid>/mem, 2026-10-30, 9 workers): 1.30 GB resident, of which 763 MB was
memory glibc had already been given back by the program and kept. Live data in malloc was 134 MB. Nine 64 MB thread
heaps held 23 MB between them: one small live object pins a heap, and glibc returns memory only from a heap's top, so a
single query that loaded a few hundred rows stayed resident for the life of the process. On a 16 GB Mac at 4.8 GB of
swap that memory is taken from the browsers.

`malloc_trim(0)` releases the free pages inside every heap. It costs a walk of the free lists and needs no restart.
Dockerfile pins the thresholds that decide how much builds up between two trims (MALLOC_MMAP_THRESHOLD_).
"""

import asyncio
import ctypes
import linecache
import os
import time

LOG_EVERY_SECONDS = 300.0
# A jump this large is logged at once: the line that names the moment a regression began.
LOG_JUMP_KB = 150 * 1024
_FIELDS = ("VmRSS", "VmHWM", "RssAnon", "Threads")


def _load_trim():
    """glibc's malloc_trim, or None on a platform without it (a native Mac run, musl)."""
    try:
        return ctypes.CDLL("libc.so.6").malloc_trim
    except (OSError, AttributeError):
        return None


_trim = _load_trim()


def trim(function=None):
    """Return free heap pages to the operating system. Seconds taken; None when the platform has no malloc_trim.

    Blocks for the walk (ctypes releases the GIL for it), so it belongs on a thread, not on the event loop."""
    function = function or _trim
    if function is None:
        return None
    started = time.perf_counter()
    function(0)
    return time.perf_counter() - started


def process_kb(pid="self", root="/proc"):
    """VmRSS, VmHWM and RssAnon in KB and the thread count of one process; {} where /proc has no such process."""
    found = {}
    try:
        with open(f"{root}/{pid}/status") as status:
            for line in status:
                name, _, value = line.partition(":")
                if name in _FIELDS:
                    found[name] = int(value.split()[0])
    except (OSError, ValueError, IndexError):
        return {}
    return found


def children_kb(pid=None, root="/proc"):
    """{name: VmRSS in KB} of this process's direct children, the Playwright driver above all (333 MB, 2026-10-30)."""
    pid = pid or os.getpid()
    found = {}
    try:
        entries = [entry for entry in os.listdir(root) if entry.isdigit()]
    except OSError:
        return found
    for entry in entries:
        try:
            with open(f"{root}/{entry}/stat") as stat:
                # The name may hold spaces and parentheses; everything after the last ")" is fixed fields.
                head, _, rest = stat.read().rpartition(")")
            if int(rest.split()[1]) != pid:
                continue
            name = head.partition("(")[2]
        except (OSError, ValueError, IndexError):
            continue
        rss = process_kb(entry, root).get("VmRSS")
        if rss is not None:
            found[name] = found.get(name, 0) + rss
    return found


def log_line(before, after, took, children):
    """One line per report: resident now, what the trim freed, how long it took, and each child process."""
    mb = lambda kb: f"{kb / 1024:.0f}MB"
    parts = [
        f"rss={mb(after.get('VmRSS', 0))}",
        f"anon={mb(after.get('RssAnon', 0))}",
        f"peak={mb(after.get('VmHWM', 0))}",
        f"threads={after.get('Threads', 0)}",
        f"freed={mb(max(0, before.get('VmRSS', 0) - after.get('VmRSS', 0)))}",
        f"trim_ms={took * 1000:.0f}" if took is not None else "trim=unavailable",
    ]
    parts += [f"child_{name}={mb(kb)}" for name, kb in sorted(children.items())]
    return "memory " + " ".join(parts)


def sweep(seen, *, clock=time.monotonic, emit=None, trim_function=None):
    """One pass: trim, then log when it is time or the process jumped. `seen` carries the last report between passes."""
    before = process_kb()
    # linecache keeps every source file a traceback ever touched, whole: 40,000+ lines of str for a stack it printed once.
    linecache.clearcache()
    took = trim(trim_function)
    after = process_kb()
    if not after:
        return None
    rss, at = after.get("VmRSS", 0), clock()
    if at - seen.get("at", -LOG_EVERY_SECONDS) >= LOG_EVERY_SECONDS or rss - seen.get("rss", 0) >= LOG_JUMP_KB:
        seen.update(at=at, rss=rss)
        (emit or (lambda line: print(line, flush=True)))(log_line(before, after, took, children_kb()))
    return took


async def watch(seconds, *, sleep=asyncio.sleep):
    """Trim on a thread every `seconds`. Nothing here may stop the app: a pass that fails is forgotten."""
    if not seconds or (_trim is None and not process_kb()):
        return
    seen = {}
    while True:
        try:
            await asyncio.to_thread(sweep, seen)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - housekeeping must never reach the application workers
            print(f"memory sweep failed: {type(exc).__name__}", flush=True)
        await sleep(seconds)
