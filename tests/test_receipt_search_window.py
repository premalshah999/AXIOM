"""The receipt search covered every unconfirmed run back to the oldest (18 days of codes and alerts) and paged through
that mail from a shared cursor, so a new acknowledgement waited for the sweep to wrap: Lyft's "Thank you for your
application" sat unread while its run stayed unconfirmed, and a one-run check resumed the same deep cursor
(2026-10-25). Synthetic runs and mail only."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from jobpilot.db import Mailbox, Run
from jobpilot.receipts import RECEIPT_SEARCH, UNKNOWN_RECEIPT_DAYS, check_receipts
from support_mail import receipt_seed


def searches(monkeypatch, service):
    queries = []

    async def request(box, path, params, background=False):
        queries.append(params["q"])
        return {"messages": []}

    monkeypatch.setattr(service.mail, "request", request)
    return queries


def after(query):
    return int(query.split("after:")[1].split()[0])


async def test_the_search_asks_only_for_acknowledgement_wording_within_the_unknown_window(service, monkeypatch):
    recent = datetime.now(UTC) - timedelta(hours=2)
    stale, fresh = receipt_seed(service, 2, state="submission_unknown", start=recent)
    with service.db.session() as s:
        s.get(Run, stale).created_at = (datetime.now(UTC) - timedelta(days=UNKNOWN_RECEIPT_DAYS + 5)).isoformat()
    queries = searches(monkeypatch, service)
    await check_receipts(service.mail)
    assert queries and RECEIPT_SEARCH in queries[0]
    assert after(queries[0]) >= int(recent.timestamp()) // 3600 * 3600


async def test_a_one_run_check_reads_its_own_window_and_leaves_the_shared_cursor(service, monkeypatch):
    early = datetime.now(UTC) - timedelta(days=3)
    first, second = receipt_seed(service, 2, state="submission_unknown", start=early)
    late = datetime.now(UTC) - timedelta(minutes=30)
    with service.db.session() as s:
        s.get(Run, second).created_at = late.isoformat()
        box = s.scalar(select(Mailbox)).id
    cursor = {"query_id": "deep", "page_token": "gm-before:17", "remaining_ids": ["gm:1"]}
    service.db.set_setting("receipt_mail_page:" + box, cursor)
    queries = searches(monkeypatch, service)
    await check_receipts(service.mail, second)
    assert after(queries[0]) == int(late.timestamp()) // 3600 * 3600
    assert service.db.get_setting("receipt_mail_page:" + box, {}) == cursor


async def test_a_one_run_check_first_searches_with_the_employers_name(service, monkeypatch):
    at = datetime.now(UTC) - timedelta(minutes=30)
    (rid,) = receipt_seed(service, 1, state="submission_unknown", start=at)
    queries = searches(monkeypatch, service)
    await check_receipts(service.mail, rid)
    assert '"Example"' in queries[0] and '"Example"' not in queries[-1]


def test_the_employer_hint_skips_generic_words():
    from jobpilot.receipts import employer_hint

    assert employer_hint("The Walt Disney Company") == '"Walt"'
    assert employer_hint("Inc.") == ""
