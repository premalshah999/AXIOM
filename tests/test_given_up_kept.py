"""The whole-history "given up" answer is kept for seconds, and only while nothing changed (2026-11-01).

/api/jobs took 5-20 s a page on a real database: every page, chip count and Overview poll recomputed, for each of ~60 stopped
jobs, about ten queries (the platform's thousands of rows, the run history with its packet's keys), 560 statements for a page.
Kept for seconds and dropped when a run or a setting appears, a poll is one signature read."""

import pytest
from sqlalchemy import insert

from jobpilot import outlook
from jobpilot.db import Job, Run

from test_pipeline_counts import at, job, run

pytestmark = pytest.mark.integration


def seed(service):
    with service.db.exclusive() as s:
        s.execute(insert(Job), [{**job(1), "ats": "custom"}, {**job(2), "ats": "custom"}])  # custom: no daily retry covers it
        s.execute(insert(Run), [run(1, "job00001", "waiting_browser", created=at(70), finished=at(60))])


def count_computations(monkeypatch):
    calls = []
    original = outlook._given_up
    monkeypatch.setattr(outlook, "_given_up", lambda session, only: calls.append(only) or original(session, only))
    return calls


def test_a_second_ask_with_nothing_changed_is_not_computed_again(service, monkeypatch):
    monkeypatch.setattr(outlook, "GIVEN_UP_SECONDS", 10)
    seed(service)
    calls = count_computations(monkeypatch)
    with service.db.session() as s:
        first = outlook.given_up(s)
    with service.db.session() as s:
        second = outlook.given_up(s)
    assert first == second == {"job00001"} and len(calls) == 1


def test_a_new_run_is_seen_at_once(service, monkeypatch):
    monkeypatch.setattr(outlook, "GIVEN_UP_SECONDS", 10)
    seed(service)
    calls = count_computations(monkeypatch)
    with service.db.session() as s:
        assert outlook.given_up(s) == {"job00001"}
    with service.db.exclusive() as s:
        s.execute(insert(Run), [run(2, "job00002", "timed_out", created=at(5), finished=at(4))])
    with service.db.session() as s:
        assert outlook.given_up(s) == {"job00001", "job00002"}
    assert len(calls) == 2


def test_a_page_of_rows_is_never_kept(service, monkeypatch):
    monkeypatch.setattr(outlook, "GIVEN_UP_SECONDS", 10)
    seed(service)
    calls = count_computations(monkeypatch)
    with service.db.session() as s:
        outlook.given_up(s, only=["job00001"])
        outlook.given_up(s, only=["job00001"])
    assert len(calls) == 2
