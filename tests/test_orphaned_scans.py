"""An update during a new install's first search retired the board scans waiting in the old build's queue, but each board's
scan flag still said queued or running: the Job board said "Searching…" for six hours and Search now skipped those boards
(clean-install run, 2026-11-01). A flag with no workflow behind it is queued again; a finished board keeps its result."""

from jobpilot.workers import orphaned_scans


def test_only_flags_with_no_workflow_behind_them_are_queued_again():
    flags = {
        "a": {"state": "queued"},     # retired with the old build: nothing runs it
        "b": {"state": "running"},    # its process went away
        "c": {"state": "complete"},   # finished: the result stands
        "d": {"state": "queued"},     # a workflow is waiting for it
        "e": {"state": "error"},
    }
    assert orphaned_scans(flags, live={"d"}) == ["a", "b"]
