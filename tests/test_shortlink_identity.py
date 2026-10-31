"""A short link is the posting it redirects to: duplicates behind link shorteners. Synthetic hosts and postings."""

import time

import httpx
import pytest
from sqlalchemy import select

from jobpilot import shortlinks
from jobpilot.admission import QueueBlocked
from jobpilot.ats import canonicalize
from jobpilot.db import Event, Job, Run, Setting

SHORT = "https://go.vansh.nyc/AB"
POSTING = "https://jobs.lever.co/examplecorp/11111111-2222-3333-4444-555555555555"


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.setattr(shortlinks, "_memo", {})
    monkeypatch.setattr(shortlinks, "_last", {})
    monkeypatch.setattr(shortlinks, "MIN_GAP", 0)
    monkeypatch.setattr(shortlinks, "TRANSPORT", None)


def network(monkeypatch, routes):
    """Serve `routes` ({short url: (status, Location)}) and count the requests."""
    calls = []

    def handler(request):
        calls.append((request.method, str(request.url)))
        status, location = routes.get(str(request.url), (404, None))
        return httpx.Response(status, headers={"location": location} if location else {})

    monkeypatch.setattr(shortlinks, "TRANSPORT", httpx.MockTransport(handler))
    return calls


def posting(url, title="New Grad Engineer"):
    from jobpilot.features.discovery.feeds import Posting

    return Posting(company="Example Corp", title=title, url=url, locations=["Remote"], posted_at="2026-10-29T00:00:00+00:00",
                   basis="verified", source="feed")


def add(service, job_id, url, *, ats, status="ready", company="Example Corp", title="Engineer I"):
    with service.db.exclusive() as s:
        s.add(Job(id=job_id, identity=canonicalize(url)[1], url=url, ats=ats, status=status, company=company, title=title))


def test_a_short_link_resolves_to_the_posting_with_one_request_and_is_remembered(monkeypatch, service):
    calls = network(monkeypatch, {SHORT: (302, POSTING)})
    assert shortlinks.resolve(SHORT, service.db) == POSTING
    assert shortlinks.resolve(SHORT, service.db) == POSTING
    assert calls == [("HEAD", SHORT)]
    # A new process answers from the saved row, without asking the shortener again.
    shortlinks._memo.clear()
    assert shortlinks.resolve(SHORT, service.db) == POSTING
    assert len(calls) == 1


@pytest.mark.parametrize(
    "answer",
    [(200, None), (404, None), (302, None), (302, "javascript:alert(1)"), (302, "http://user:pw@host.test/job/1")],
)
def test_a_failed_lookup_is_none_and_not_repeated_within_the_hour(monkeypatch, service, answer):
    calls = network(monkeypatch, {SHORT: answer})
    assert shortlinks.resolve(SHORT, service.db) is None
    assert shortlinks.resolve(SHORT, service.db) is None
    assert len(calls) == 1
    with service.db.session() as s:
        assert shortlinks.cached(s, SHORT) == (True, None)


def test_a_network_error_is_a_failed_lookup_not_an_exception(monkeypatch, service):
    def boom(request):
        raise httpx.ConnectTimeout("slow")

    monkeypatch.setattr(shortlinks, "TRANSPORT", httpx.MockTransport(boom))
    assert shortlinks.resolve(SHORT, service.db) is None


def test_a_link_that_refuses_head_is_asked_once_more_without_a_body(monkeypatch, service):
    seen = []

    def handler(request):
        seen.append(request.method)
        if request.method == "HEAD":
            return httpx.Response(405)
        return httpx.Response(302, headers={"location": POSTING})

    monkeypatch.setattr(shortlinks, "TRANSPORT", httpx.MockTransport(handler))
    assert shortlinks.resolve(SHORT, service.db) == POSTING
    assert seen == ["HEAD", "GET"]


def test_a_chain_of_short_links_ends_at_the_first_other_host(monkeypatch, service):
    other = "https://bit.ly/zz"
    calls = network(monkeypatch, {SHORT: (301, other), other: (302, POSTING)})
    assert shortlinks.resolve(SHORT, service.db) == POSTING
    assert [c[1] for c in calls] == [SHORT, other]  # The employer's page itself is never requested.


def test_only_short_link_hosts_are_resolved(monkeypatch, service):
    calls = network(monkeypatch, {})
    assert shortlinks.resolve(POSTING, service.db) is None
    assert shortlinks.resolve("https://simplify.jobs/p/abc", service.db) is None  # An aggregator page, not a redirect.
    assert calls == []


def test_requests_to_one_host_are_spaced(monkeypatch, service):
    monkeypatch.setattr(shortlinks, "MIN_GAP", 0.15)
    network(monkeypatch, {SHORT: (302, POSTING), SHORT + "2": (302, POSTING)})
    started = time.monotonic()
    shortlinks.resolve(SHORT, service.db)
    shortlinks.resolve(SHORT + "2", service.db)
    assert time.monotonic() - started >= 0.14


async def test_a_short_link_to_a_confirmed_posting_is_recorded_as_already_applied_without_a_browser(
    service, monkeypatch
):
    """Shield AI: the feed's link ran after the same requisition on Lever was confirmed (2026-10-29)."""
    network(monkeypatch, {SHORT: (302, POSTING)})
    add(service, "posting", POSTING, ats="lever", status="confirmed")
    add(service, "short", SHORT, ats="custom")
    with service.db.exclusive() as s:
        s.add(Run(job_id="posting", state="confirmed", mode="submit", packet={}))
    with pytest.raises(QueueBlocked) as err:
        await service.queue("short", "submit", owner_pick=True)
    assert err.value.code == "duplicate_posting"
    with service.db.session() as s:
        job = s.get(Job, "short")
        runs = list(s.scalars(select(Run).where(Run.job_id == "short")))
        assert job.status == "already_applied"
        assert [r.state for r in runs] == ["already_applied"]
        assert runs[0].receipt["type"] == "duplicate_posting" and runs[0].receipt["duplicate_of"] == "posting"
        assert s.scalar(select(Event.kind).where(Event.run_id == runs[0].id)) == "duplicate_posting"
        assert not list(s.scalars(select(Run).where(Run.state.in_(["queued", "running"]))))


async def test_the_posting_itself_is_blocked_when_its_short_link_was_applied_to(service, monkeypatch):
    """The other direction: the feed's link was applied through the custom adapter first, the employer's own URL
    arrives later as a second job row."""
    network(monkeypatch, {SHORT: (302, POSTING)})
    add(service, "posting", POSTING, ats="lever")
    add(service, "short", SHORT, ats="custom", status="confirmed")
    with service.db.exclusive() as s:
        s.add(Run(job_id="short", state="confirmed", mode="submit", packet={}))
    shortlinks.resolve(SHORT, service.db)  # Asked when the link was first queued.
    with pytest.raises(QueueBlocked) as err:
        await service.queue("posting", "submit", owner_pick=True)
    assert err.value.code == "duplicate_posting"
    with service.db.session() as s:
        assert s.get(Job, "posting").status == "already_applied"


async def test_a_short_link_to_an_unapplied_posting_is_not_blocked(service, monkeypatch):
    network(monkeypatch, {SHORT: (302, POSTING)})
    add(service, "posting", POSTING, ats="lever")
    add(service, "short", SHORT, ats="custom")
    try:
        await service.queue("short", "submit", owner_pick=True)
    except QueueBlocked as err:  # Later gates may hold it; never the duplicate guard.
        assert err.code not in {"duplicate_posting", "submitted_or_uncertain"}
    except ValueError:  # Past admission: no resume in this synthetic database.
        pass
    with service.db.session() as s:
        assert s.get(Job, "short").status != "already_applied"


async def test_a_failed_lookup_does_not_block_the_application(service, monkeypatch):
    calls = network(monkeypatch, {SHORT: (500, None)})
    add(service, "short", SHORT, ats="custom")
    try:
        await service.queue("short", "submit", owner_pick=True)
    except QueueBlocked as err:
        assert err.code not in {"duplicate_posting", "submitted_or_uncertain"}
    except ValueError:
        pass
    assert len(calls) == 1
    with service.db.session() as s:
        assert shortlinks.cached(s, SHORT) == (True, None)


async def test_an_applied_short_link_that_never_resolved_is_still_protected_by_its_own_identity(service, monkeypatch):
    network(monkeypatch, {SHORT: (500, None)})
    add(service, "short", SHORT, ats="custom", status="confirmed")
    with service.db.exclusive() as s:
        s.add(Run(job_id="short", state="confirmed", mode="submit", packet={}))
    with pytest.raises(QueueBlocked) as err:
        await service.queue("short", "submit", owner_pick=True)
    assert err.value.code == "submitted_or_uncertain"


def test_ingest_stores_the_posting_not_the_short_link(service, monkeypatch):
    from jobpilot.features.discovery.feeds import ingest

    network(monkeypatch, {SHORT: (302, POSTING)})
    add(service, "posting", POSTING, ats="lever", status="confirmed")
    counts = ingest(service.db, [posting(SHORT, "New Grad: Engineer I")])
    assert counts["existing"] == 1 and counts["created"] == 0
    with service.db.session() as s:
        assert list(s.scalars(select(Job.id).where(Job.url == SHORT))) == []  # No second row for the same posting.


def test_ingest_bounds_its_lookups_and_keeps_the_link_for_the_rest(service, monkeypatch):
    from jobpilot.features.discovery import feeds

    monkeypatch.setattr(feeds, "MAX_SHORTLINK_LOOKUPS", 1)
    routes = {
        f"https://go.vansh.nyc/{c}": (302, f"https://jobs.lever.co/examplecorp/{c * 8}-2222-3333-4444-555555555555")
        for c in "ab"
    }
    calls = network(monkeypatch, routes)
    feeds.ingest(service.db, [posting(u) for u in routes])
    assert len(calls) == 1
    with service.db.session() as s:
        urls = set(s.scalars(select(Job.url)))
    assert any(u.startswith("https://jobs.lever.co/") for u in urls) and "https://go.vansh.nyc/b" in urls


def test_a_recorded_duplicate_takes_no_employer_slot(service):
    from jobpilot.application_pacing import LIMIT_KEY, require_company_limit

    service.db.set_setting(LIMIT_KEY, {"limit": 2})
    add(service, "posting", POSTING, ats="lever", status="confirmed")
    add(service, "short", SHORT, ats="custom", status="already_applied")
    add(service, "next", "https://jobs.lever.co/examplecorp/99999999-2222-3333-4444-555555555555", ats="lever")
    with service.db.exclusive() as s:
        s.add(Run(job_id="posting", state="confirmed", mode="submit", packet={}))
        s.add(Run(job_id="short", state="already_applied", mode="submit", packet={}, receipt={"type": "duplicate_posting"}))
    with service.db.session() as s:
        require_company_limit(s, s.get(Job, "next"))  # One application holds one of the two slots.
        s.get(Run, s.scalar(select(Run.id).where(Run.job_id == "short"))).receipt = {"type": "employer_already_applied"}
        s.flush()
        with pytest.raises(QueueBlocked):  # The employer's own "already applied" is a second application.
            require_company_limit(s, s.get(Job, "next"))
