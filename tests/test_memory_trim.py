"""The app gives freed heap back and says what it holds (2026-10-30).

A census of production's heap: 1.30 GB resident, 763 MB of it memory the program had already freed, and glibc never
returned it because one small live object pins a whole 64 MB thread heap. Nothing logged the process's size, so the
growth showed up only as swap and timeouts."""

import asyncio

import pytest

from jobpilot import memory


def fake_proc(tmp_path, pid, name, rss, ppid=1, threads=4):
    folder = tmp_path / str(pid)
    folder.mkdir()
    (folder / "stat").write_text(f"{pid} ({name}) S {ppid} 1 1 0 -1 0 0 0\n")
    (folder / "status").write_text(f"Name:\t{name}\nVmHWM:\t{rss + 10} kB\nVmRSS:\t{rss} kB\nRssAnon:\t{rss} kB\nThreads:\t{threads}\n")


def test_a_trim_passes_zero_padding_and_reports_its_time():
    calls = []
    took = memory.trim(lambda pad: calls.append(pad))
    assert calls == [0] and took >= 0


def test_a_platform_without_malloc_trim_is_a_quiet_no_op(monkeypatch):
    monkeypatch.setattr(memory, "_trim", None)
    assert memory.trim() is None


def test_status_fields_are_read_and_a_vanished_process_is_empty(tmp_path):
    fake_proc(tmp_path, 12, "jobpilot", 2048, threads=154)
    assert memory.process_kb(12, tmp_path) == {"VmHWM": 2058, "VmRSS": 2048, "RssAnon": 2048, "Threads": 154}
    assert memory.process_kb(99, tmp_path) == {}


def test_children_are_summed_by_name_and_a_name_with_spaces_is_not_misread(tmp_path):
    fake_proc(tmp_path, 7, "jobpilot", 1000)
    fake_proc(tmp_path, 8, "node driver (x)", 300, ppid=7)
    fake_proc(tmp_path, 9, "node driver (x)", 200, ppid=7)
    fake_proc(tmp_path, 10, "other", 500, ppid=1)
    assert memory.children_kb(7, tmp_path) == {"node driver (x)": 500}


def test_the_log_line_names_what_a_regression_needs():
    before, after = {"VmRSS": 2048 * 1024}, {"VmRSS": 1024 * 1024, "RssAnon": 900 * 1024, "VmHWM": 2048 * 1024, "Threads": 150}
    line = memory.log_line(before, after, 0.004, {"MainThread": 300 * 1024})
    assert line.startswith("memory ")
    for part in ("rss=1024MB", "anon=900MB", "peak=2048MB", "threads=150", "freed=1024MB", "trim_ms=4", "child_MainThread=300MB"):
        assert part in line


def test_the_sweep_logs_on_schedule_and_on_a_jump_not_on_every_pass(monkeypatch):
    sizes = iter([1000, 1000, 1000, 1000, 1000, 400_000, 400_000, 400_000])
    monkeypatch.setattr(memory, "process_kb", lambda *a: {"VmRSS": next(sizes)})
    monkeypatch.setattr(memory, "children_kb", lambda *a: {})
    lines, seen, clock = [], {}, {"t": 1000.0}

    def sweep():
        memory.sweep(seen, clock=lambda: clock["t"], emit=lines.append, trim_function=lambda pad: None)

    sweep()
    assert len(lines) == 1  # the first pass reports where the process starts
    clock["t"] += 60
    sweep()
    assert len(lines) == 1  # a minute later, nothing has changed
    clock["t"] += 60
    sweep()
    sweep()
    assert len(lines) == 2  # 390 MB more is logged at once, not five minutes later


def test_a_sweep_clears_the_source_cache_a_traceback_filled(monkeypatch):
    import linecache

    linecache.getline(memory.__file__, 1)
    assert memory.__file__ in linecache.cache
    memory.sweep({}, emit=lambda line: None, trim_function=lambda pad: None)
    assert memory.__file__ not in linecache.cache


async def test_the_watch_survives_a_failing_pass_and_stops_when_cancelled(monkeypatch):
    passes = []

    def sweep(seen):
        passes.append(seen)
        raise OSError("gone")

    monkeypatch.setattr(memory, "sweep", sweep)
    monkeypatch.setattr(memory, "_trim", lambda pad: None)  # a Mac has no glibc: the loop would not start

    async def sleep(_seconds):
        if len(passes) >= 2:
            raise asyncio.CancelledError
        await asyncio.sleep(0)

    with pytest.raises(asyncio.CancelledError):
        await memory.watch(60, sleep=sleep)
    assert len(passes) == 2


async def test_zero_seconds_turns_it_off(monkeypatch):
    monkeypatch.setattr(memory, "sweep", lambda seen: pytest.fail("must not run"))
    await memory.watch(0)


def test_the_image_pins_the_allocator_thresholds():
    from pathlib import Path

    dockerfile = (Path(__file__).parent.parent / "Dockerfile").read_text()
    for setting in ("MALLOC_ARENA_MAX=2", "MALLOC_MMAP_THRESHOLD_=131072", "MALLOC_TRIM_THRESHOLD_=131072"):
        assert setting in dockerfile
