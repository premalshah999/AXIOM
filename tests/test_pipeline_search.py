"""A search page reads its matches first, then the page (2026-11-01).

On a real database a Jobs search took 6 s where the same page unsearched took 1.5 s: the text filter sat inside the one big
query, PostgreSQL took it for a single row and joined each job to the whole run history one job at a time (3,931 sorts of 9,667
runs). The ids the search matches are read on their own and the page is filtered by them, so the plan sees a list."""

import pytest
from sqlalchemy import event, insert

from jobpilot.api import pipeline
from jobpilot.db import Job, Run

from test_pipeline_counts import at, job, run

pytestmark = pytest.mark.integration


def seed(service):
    jobs = [{**job(n), "title": title} for n, title in enumerate(["Data Engineer", "Platform Engineer", "Nurse", "Data Analyst"], 1)]
    with service.db.exclusive() as s:
        s.execute(insert(Job), jobs)
        s.execute(insert(Run), [run(n, f"job{n:05d}", "confirmed", created=at(50 + n), finished=at(40 + n)) for n in range(1, 5)])


def titles(service, q):
    with service.db.session() as s:
        page = pipeline.page(s, "all", q, 1, 25)
    return sorted(r["job"]["title"] for r in page["rows"]), page["total"]


def test_a_search_lists_the_matches_and_counts_them(service):
    seed(service)
    assert titles(service, "engineer") == (["Data Engineer", "Platform Engineer"], 2)
    assert titles(service, "DATA") == (["Data Analyst", "Data Engineer"], 2)
    assert titles(service, "") == (["Data Analyst", "Data Engineer", "Nurse", "Platform Engineer"], 4)


def test_a_search_that_matches_nothing_is_an_empty_page(service):
    seed(service)
    assert titles(service, "astronaut") == ([], 0)


def test_the_text_filter_is_not_inside_the_page_query(service):
    seed(service)
    seen = []

    @event.listens_for(service.db.engine, "before_cursor_execute")
    def record(conn, cursor, statement, parameters, context, executemany):
        seen.append((statement, str(parameters)))

    try:
        titles(service, "engineer")
    finally:
        event.remove(service.db.engine, "before_cursor_execute", record)
    page_queries = [(sql, values) for sql, values in seen if "count(*) over" in sql.lower()]
    assert page_queries and not any("engineer" in values.lower() for _sql, values in page_queries)
