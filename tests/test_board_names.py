"""A new install showed 484 of 738 jobs under a lowercase slug and 78 under a UUID, while the owner's database hid it: postings there had taken
their names from other lists. The employer comes from the board itself; an id never reaches a screen. Synthetic data only."""

import asyncio
import json
import re

import pytest

from jobpilot import board_names, board_view
from jobpilot.board_names import NOT_LISTED, from_ashby_page, from_title, named, slug_like, tidied, usable
from jobpilot.db import Job, JobListing, Source
from jobpilot.discovery import add_job
from jobpilot.jobboard import CATALOG, scrape_source, setup_sources
from jobpilot.posting_identity import placeholder_company
from jobpilot.schemas import JobInput, Profile
from tests.test_board_view import listing, rows, setup

BOARD_ID = "0a1b2c3d-1111-4222-8333-444455556666"
# The real page's shape: the data object, then more of the same script after it.
ASHBY_PAGE = '<html><head><title>x Jobs</title></head><body><script>window.__appData = {"organization":{"name":"%s","publicWebsite":"https://example.test/"},"posting":null};\n      window.__nonce = "a}";</script></body></html>'


def source(service, sid, name, kind="ashby", url=None):
    url = url or f"https://jobs.ashbyhq.com/{name}"
    with service.db.exclusive() as s:
        s.add(Source(id=sid, name=name, kind=kind, url=url, cron="0 * * * *"))
    return {"id": sid, "name": name, "kind": kind, "url": url}


def saved_name(service, sid):
    with service.db.session() as s:
        return s.get(Source, sid).name


def counted(answers):
    calls = []

    async def fetch(url, **_k):
        calls.append(url)
        for part, body in answers.items():
            if part in url:
                if isinstance(body, Exception):
                    raise body
                return body, url
        raise RuntimeError("no answer for " + url)

    return fetch, calls


@pytest.mark.parametrize("name,slug", [("quora", True), ("generalintuition-medal", True), ("OpenAI", False), ("Grow Therapy", False), ("acme corp", False), ("", False), ("1234", False)])
def test_a_slug_is_a_name_as_an_address_spells_it(name, slug):
    assert slug_like(name) is slug


def test_tidying_only_raises_first_letters():
    assert tidied("quora") == "Quora" and tidied("general-intuition") == "General Intuition" and tidied("acme_labs") == "Acme Labs"


@pytest.mark.parametrize("value", [BOARD_ID, BOARD_ID.upper(), "", "  ", NOT_LISTED, "company not listed"])
def test_an_id_or_the_screens_words_are_no_employer(value):
    assert placeholder_company(value, "https://jobs.ashbyhq.com/x") is True


@pytest.mark.parametrize("value", ["Quora", "Booking.com", "3M", "quora"])
def test_a_name_stays_a_name(value):
    assert placeholder_company(value, "https://jobs.ashbyhq.com/x") is False


def test_what_a_board_states_counts_unless_it_is_its_own_id_or_slug():
    assert usable("Quora", "quora") == "Quora" and usable("OpenAI", "openai") == "OpenAI"
    assert usable(BOARD_ID, BOARD_ID) == "" and usable("quora", "quora") == "" and usable("", "x") == ""


def test_an_ashby_page_and_a_lever_title_name_the_employer():
    assert from_ashby_page(ASHBY_PAGE % "Example Labs") == "Example Labs"
    assert from_ashby_page("<html></html>") == "" and from_ashby_page("window.__appData = {broken};</script>") == ""
    assert from_title("<html><title>Example &amp; Sons</title></html>") == "Example & Sons"


def test_a_slug_named_board_asks_its_page_once_and_its_jobs_take_the_name(service):
    src = source(service, "s1", "examplelabs")
    with service.db.exclusive() as s:
        s.add(Job(id="old", identity="old", url="https://jobs.ashbyhq.com/examplelabs/1", company="examplelabs", title="Software Engineer", ats="ashby", status="new", location="Remote"))
        s.add(Job(id="own", identity="own", url="https://jobs.ashbyhq.com/examplelabs/2", company="A Name Of Its Own", title="Software Engineer", ats="ashby", status="new", location="Remote"))
        for jid in ("old", "own"):
            s.add(JobListing(job_id=jid, source_id="s1", posted_at="2026-11-01T00:00:00+00:00", date_basis="x", location="Remote", career_level="x", match_reason="x"))
    fetch, calls = counted({"jobs.ashbyhq.com/examplelabs": ASHBY_PAGE % "Example Labs"})
    assert asyncio.run(named(src, service.db, fetch))["name"] == "Example Labs"
    assert saved_name(service, "s1") == "Example Labs"
    with service.db.session() as s:
        assert s.get(Job, "old").company == "Example Labs" and s.get(Job, "own").company == "A Name Of Its Own"
    # The next scan has a real name, and asks nothing.
    assert asyncio.run(named({**src, "name": "Example Labs"}, service.db, fetch))["name"] == "Example Labs"
    assert len(calls) == 1


def test_a_page_that_cannot_be_read_gives_a_tidied_slug_and_is_not_asked_again_at_once(service):
    src = source(service, "s2", "examplelabs")
    fetch, calls = counted({"jobs.ashbyhq.com": RuntimeError("down")})
    assert asyncio.run(named(src, service.db, fetch))["name"] == "Examplelabs"
    assert asyncio.run(named(src, service.db, fetch))["name"] == "Examplelabs"
    assert len(calls) == 1 and saved_name(service, "s2") == "examplelabs"


def test_a_board_whose_own_name_is_an_id_has_no_name_and_the_row_says_so(service, config, client, monkeypatch):
    """Its postings are real, and an id never reaches a screen: not on a row, a filter or the answer anywhere."""
    setup(service)
    src = source(service, "s3", BOARD_ID, url=f"https://jobs.ashbyhq.com/{BOARD_ID}")
    posted = (__import__("datetime").datetime.now(__import__("datetime").UTC) - __import__("datetime").timedelta(days=2)).isoformat()
    board = {"jobs": [{"title": "Software Engineer", "applyUrl": f"https://jobs.ashbyhq.com/{BOARD_ID}/11111111-2222-4333-8444-555555555555", "location": "Austin, TX", "publishedAt": posted,
                       "address": {"postalAddress": {"addressCountry": "United States"}}, "descriptionPlain": "Build things. " * 30}]}
    fetch, _calls = counted({"api.ashbyhq.com": json.dumps(board), f"jobs.ashbyhq.com/{BOARD_ID}": ASHBY_PAGE % BOARD_ID})
    monkeypatch.setattr("jobpilot.jobboard.fetch", fetch)
    service.db.set_setting("job_board_settings", {"enabled": True, "max_age_hours": 720})
    asyncio.run(scrape_source(src, service.db, config))
    with service.db.session() as s:
        assert [row.company for row in s.query(Job).all()] == [""]
    got = rows(service, fit="all")
    assert [r["company"] for r in got["rows"]] == [NOT_LISTED]
    assert BOARD_ID not in json.dumps({**got, "rows": [{k: v for k, v in r.items() if k != "url"} for r in got["rows"]]})


def test_two_employers_without_a_name_are_not_one_posting(service):
    setup(service)
    listing(service, "one", company="", url="https://jobs.one.example.test/1")
    listing(service, "two", company="", url="https://jobs.two.example.test/2")
    assert {r["id"] for r in rows(service, fit="all", group=False)["rows"]} == {"one", "two"}


def test_greenhouse_names_itself_in_the_list_with_no_extra_request(service, config, monkeypatch):
    src = source(service, "s4", "examplelabs", kind="greenhouse", url="https://job-boards.greenhouse.io/examplelabs")
    service.db.set_setting("profile", Profile(target_roles=["Software Engineer"]).model_dump())
    fetch, calls = counted({"boards-api.greenhouse.io": json.dumps({"jobs": [{"id": 1, "title": "Welder", "company_name": "Example Labs", "location": {"name": "Berlin"}}]})})
    monkeypatch.setattr("jobpilot.jobboard.fetch", fetch)
    asyncio.run(scrape_source(src, service.db, config))
    assert saved_name(service, "s4") == "Example Labs"
    assert all("boards-api.greenhouse.io" in url for url in calls) and len(calls) == 1


def test_a_name_someone_gave_a_board_stays(service, config, monkeypatch):
    src = source(service, "s5", "Their Own Name", kind="greenhouse", url="https://job-boards.greenhouse.io/examplelabs")
    fetch, _ = counted({"boards-api.greenhouse.io": json.dumps({"jobs": [{"id": 1, "title": "Welder", "company_name": "Example Labs", "location": {"name": "Berlin"}}]})})
    monkeypatch.setattr("jobpilot.jobboard.fetch", fetch)
    service.db.set_setting("profile", Profile(target_roles=["Software Engineer"]).model_dump())
    asyncio.run(scrape_source(src, service.db, config))
    assert saved_name(service, "s5") == "Their Own Name"


def test_no_id_is_stored_as_an_employer_from_any_path(service):
    job, _ = add_job(service.db, JobInput(url=f"https://jobs.ashbyhq.com/{BOARD_ID}/99999999-2222-4333-8444-555555555555", title="Software Engineer", company=BOARD_ID, location="Remote"))
    assert job["company"] == ""
    job, _ = add_job(service.db, JobInput(url="https://careers.example.test/jobs/12345", title="Software Engineer", company="careers.example.test", location="Remote"))
    assert job["company"] == ""
    job, _ = add_job(service.db, JobInput(url="https://jobs.lever.co/booking/12345678", title="Software Engineer", company="Booking.com", location="Remote"))
    assert job["company"] == "Booking.com"


def test_an_id_already_saved_is_cleared_at_the_next_start(service):
    with service.db.exclusive() as s:
        s.add(Job(id="saved", identity="saved", url="https://jobs.ashbyhq.com/x/1", company=BOARD_ID, title="Software Engineer", ats="ashby", status="new", location="Remote"))
        s.add(Job(id="named", identity="named", url="https://jobs.ashbyhq.com/x/2", company="Example Labs", title="Software Engineer", ats="ashby", status="new", location="Remote"))
        s.add(Source(id="s6", name=BOARD_ID, kind="ashby", url=f"https://jobs.ashbyhq.com/{BOARD_ID}", cron="0 * * * *"))
    service.no_ids_as_employers()
    with service.db.session() as s:
        assert s.get(Job, "saved").company == "" and s.get(Job, "named").company == "Example Labs" and s.get(Source, "s6").name == NOT_LISTED


def test_the_catalog_names_no_board_by_an_id_and_an_empty_install_stores_none(service):
    names = [entry[0] if isinstance(entry, tuple) else entry for entries in CATALOG.values() for entry in entries]
    assert len(names) > 500 and not [n for n in names if re.fullmatch(r"[0-9a-f]{8}-[0-9a-f-]{27}", n)]
    setup_sources(service.db)
    with service.db.session() as s:
        assert not [row.name for row in s.query(Source).all() if re.fullmatch(r"[0-9a-f]{8}-[0-9a-f-]{27}", row.name)]


def test_reserving_no_name_is_refused_in_words(client):
    answer = client.post("/api/reserved/employers", json={"text": NOT_LISTED})
    assert answer.status_code == 422 and "does not name its company" in answer.json()["detail"]


def test_a_stored_name_is_never_replaced_by_a_screen_word(service):
    assert board_names.usable(NOT_LISTED, "x") == ""
    assert board_view.key_of({"company": NOT_LISTED, "title": "Software Engineer", "location": "Remote", "url": "https://a.example.test/1"})[0] == "a.example.test"
