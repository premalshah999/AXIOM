"""The Job board shows real, open postings, one row each, and says what will happen to them. Synthetic data only."""

from datetime import UTC, datetime, timedelta

import pytest

from jobpilot import board_view
from jobpilot.application_pacing import LIMIT_KEY
from jobpilot.db import Job, JobListing, Run
from jobpilot.schemas import Profile

pytestmark = pytest.mark.integration


def setup(service):
    service.db.set_setting("profile", Profile(target_roles=["Software Engineer", "Data Engineer"]).model_dump())
    service.db.set_setting("job_board_settings", {"enabled": True, "auto_apply": True, "minimum_auto_fit": 45, "max_age_hours": 720, "early_career_only": True})
    service.db.set_setting(LIMIT_KEY, {"limit": 25})


def listing(service, jid, company="Alpha Example", title="Software Engineer", status="ready", score=80, location="Austin, TX", basis="Employer first_published", url=None, active=True, reason="Backend role matches Python and SQL.", age=1, ats="greenhouse"):
    seen = (datetime.now(UTC) - timedelta(hours=age)).isoformat()
    now = datetime.now(UTC).isoformat()
    slug = (company or "x").replace(" ", "-").lower()
    with service.db.exclusive() as s:
        s.add(Job(id=jid, identity=jid, url=url or f"https://boards.example.test/{slug}/jobs/{jid}", ats=ats, company=company, title=title, status=status, score=score,
                  reason=reason, demo=False, location=location, created_at=seen, description="Build things. " * 40))
        s.add(JobListing(job_id=jid, source_id="feed:new-grad" if "list" in basis else "src", posted_at=seen, date_basis=basis, location=location, country="US",
                         career_level="New grad", match_reason="x", first_seen=now, last_seen=now, active=active))
    return jid


def rows(service, **filters):
    board_view.board(service).value = None
    board_view.board(service).value = board_view.collect(service)
    return board_view.page(service, **filters)


def test_blank_closed_and_gone_rows_never_reach_the_board(service):
    setup(service)
    good = listing(service, "good")
    listing(service, "blank-title", company="Beta Example", title="")
    listing(service, "blank-company", company="", title="Data Engineer")
    listing(service, "closed", company="Gamma Example", status="closed")
    listing(service, "taken-down", company="Delta Example", active=False)
    got = rows(service, fit="all")
    assert [r["id"] for r in got["rows"]] == [good]
    assert got["hidden"]["blank"] == 2


def test_a_job_with_a_run_is_not_on_the_board(service):
    setup(service)
    listing(service, "free", company="Alpha Example")
    tried = listing(service, "tried", company="Beta Example")
    with service.db.exclusive() as s:
        s.add(Run(id="r1", job_id=tried, state="waiting_browser", mode="submit", packet={}))
    assert [r["id"] for r in rows(service, fit="all")["rows"]] == ["free"]


def test_one_posting_under_two_links_is_one_row_with_the_employers_own_link(service):
    setup(service)
    listing(service, "short", url="https://go.vansh.nyc/abc", score=90)
    direct = listing(service, "direct", url="https://boards.example.test/alpha/jobs/9", score=60)
    got = rows(service, fit="all")
    assert got["all"] == got["total"] == 1
    assert got["rows"][0]["url"].startswith("https://boards.example.test/")
    assert got["hidden"]["twin"] >= 1 and direct


def test_every_filter_count_equals_the_rows_it_opens(service):
    setup(service)
    listing(service, "a", company="Alpha Example", title="Software Engineer", location="Austin, TX")
    listing(service, "b", company="Beta Example", title="Data Engineer", location="Seattle, WA", ats="workday")
    listing(service, "c", company="Beta Example", title="Software Engineer", location="Remote", ats="workday", age=24 * 10)
    listing(service, "d", company="Gamma Example", title="Mechanical Engineer", status="skipped", score=20, reason="Mechanical role; not your field.")
    facets = rows(service, fit="all")["facets"]
    for name, arg in (("company", "company"), ("place", "place"), ("platform", "platform"), ("role", "role"), ("posted", "posted"), ("fit", "fit"), ("level", "level")):
        for option in facets[name]:
            assert rows(service, **{"fit": "all", arg: option["value"]})["total"] == option["count"], (name, option)


def test_each_row_says_why_it_fits_where_it_came_from_and_its_place_in_line(service):
    setup(service)
    listing(service, "a", reason="Owner approved: Backend role matches Python and SQL.", basis="simplify (verified)")
    listing(service, "b", company="Beta Example", status="skipped", score=0, reason="Explicit hiring restriction | Eligibility check: citizenship: x")
    got = {r["id"]: r for r in rows(service, fit="all")["rows"]}
    assert got["a"]["line"] == "Backend role matches Python and SQL."
    assert got["a"]["source"] == "Simplify list" and got["a"]["dated"] is True
    assert got["a"]["state"] == "queue" and got["a"]["position"] == 1 and got["a"]["can_add"] is False
    # The posting rules them out: the queue would only refuse it, so it is not offered.
    assert got["b"]["state"] == "below" and got["b"]["can_add"] is False and got["b"]["score"] is None


def test_a_date_the_app_inferred_is_not_called_posted():
    assert board_view.dated({"date_basis": "speedyapply-swe (inferred)"}) is False
    assert board_view.dated({"date_basis": "added"}) is False
    assert board_view.dated({"date_basis": "board:ashby (verified)"}) is True


def test_places_read_to_states_and_remote():
    assert board_view.places_of("San Jose, CA") == ["CA"]
    assert board_view.places_of("Washington, D.C.") == ["DC"]
    assert board_view.places_of("Austin, TX; San Jose, CA") == ["TX", "CA"]
    assert board_view.places_of("Remote - Seattle, Washington") == ["Remote", "WA"]
    assert board_view.places_of("San Francisco") == ["CA"]
    assert board_view.places_of("Headquarters") == []


def test_the_board_says_how_many_boards_of_the_search_are_read(service):
    at = datetime.now(UTC).isoformat()
    service.db.set_setting("job_board_requested", {"at": at})
    for i, state in enumerate(["complete", "error", "queued", "running"]):
        # A board that has run carries its own start time, after the request.
        service.db.set_setting(f"job_board_scan:s{i}", {"state": state, "started_at": at if state in {"queued"} else datetime.now(UTC).isoformat()})
    service.db.set_setting("job_board_scan:old", {"state": "complete", "started_at": "2026-01-01T00:00:00+00:00"})
    got = rows(service)
    assert (got["scanning"], got["scan_read"], got["scan_total"]) == (2, 2, 4)
