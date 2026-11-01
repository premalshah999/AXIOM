"""Ready means "can start" (2026-11-01).

At 05:20 UTC nothing ran while 62 never-tried Ready jobs sat there and about 4 could start: the rest were at the employer
cap, internships, a reserved employer's, or outside the profile's roles. The page said "Ready" for all of them and idle
runners had no explanation. `startable.read` sorts the Ready jobs with the dispatcher's own screening. Synthetic data only.
"""

from datetime import UTC, datetime, timedelta

import pytest

from jobpilot import startable
from jobpilot.db import Job, JobListing, Run, Setting
from jobpilot.schemas import Profile

pytestmark = pytest.mark.integration


def ready(service, n, title="Software Engineer", company=None, url=None, listed=True):
    jid = f"ready{n:03d}"
    seen = datetime.now(UTC).isoformat()
    with service.db.exclusive() as s:
        s.add(Job(id=jid, identity=jid, url=url or f"https://boards.example.test/co{n}/jobs/{n}", ats="greenhouse",
                  company=company or f"Company {n}", title=title, status="ready", score=88, demo=False,
                  location="Austin, TX", created_at=seen, description="Build things. " * 40))
        if listed:
            s.add(JobListing(job_id=jid, source_id="src", posted_at=seen, date_basis="Employer first_published",
                             location="Austin, TX", country="US", career_level="New grad", match_reason="x",
                             first_seen=seen, last_seen=seen, active=True))
    return jid


def profile(service, **values):
    service.db.set_setting("profile", Profile(target_roles=["Software Engineer"], **values).model_dump())
    service.db.set_setting("job_board_settings", {"minimum_auto_fit": 45, "max_age_hours": 720, "early_career_only": False})


def test_ready_counts_only_what_can_start_and_each_held_job_says_why(service):
    profile(service)
    start = ready(service, 1)
    intern = ready(service, 2, title="Software Engineer Intern")
    wrong = ready(service, 3, title="Dental Hygienist")
    old = ready(service, 4, listed=False)  # no live listing and older than the window
    with service.db.exclusive() as s:
        s.get(Job, old).created_at = (datetime.now(UTC) - timedelta(days=90)).isoformat()
    got = startable.read(service)
    assert got["startable"] == 1 and got["ready"] == 4
    assert got["jobs"][intern]["code"] == "internship"
    assert got["jobs"][wrong]["code"] == "role" and "Dental Hygienist" in got["jobs"][wrong]["reason"]
    assert got["jobs"][old]["code"] == "aged"
    assert start not in got["jobs"]
    # Every held job also says what happens next.
    assert all(job["next"] for job in got["jobs"].values())
    codes = {h["code"]: h["count"] for h in got["held"]}
    assert codes == {"internship": 1, "role": 1, "aged": 1}


def test_a_job_at_the_employer_limit_is_held_with_the_dispatchers_own_message(service):
    profile(service)
    service.db.set_setting("application_pacing:company_limit", {"limit": 2})
    for n in range(2):  # two confirmed applications at one employer
        jid = f"done{n}"
        with service.db.exclusive() as s:
            s.add(Job(id=jid, identity=jid, url=f"https://boards.example.test/capped/jobs/{n}", ats="greenhouse",
                      company="Capped Example", title="Engineer", status="confirmed", demo=False))
            s.add(Run(id=f"run{n}", job_id=jid, state="confirmed", mode="submit", packet={}))
    capped = ready(service, 7, company="Capped Example", url="https://boards.example.test/capped/jobs/77")
    open_ = ready(service, 8)
    got = startable.read(service)
    assert got["jobs"][capped]["code"] == "employer_limit" and "limit 2" in got["jobs"][capped]["reason"]
    assert open_ not in got["jobs"] and got["startable"] == 1


def test_nothing_startable_says_why_runners_are_idle_in_the_same_tally(service):
    profile(service)
    for n in range(3):
        ready(service, n, title="Software Engineer Intern")
    ready(service, 9, title="Dental Hygienist")
    got = startable.read(service)
    assert got["startable"] == 0
    assert got["idle"] == "Nothing to run: 3 internships, 1 outside your target roles."
    # With something startable there is nothing to explain.
    ready(service, 10)
    assert startable.idle_line(1, got["held"]) is None


def test_a_held_job_carries_its_reason_on_the_discovered_list(client, service):
    profile(service)
    intern = ready(service, 2, title="Software Engineer Intern")
    assert client.get("/api/startable").json()["startable"] == 0  # reads once, then the list can use it
    rows = {j["id"]: j for j in client.get("/api/job-board?include_sources=false").json()["jobs"]}
    assert rows[intern]["hold"]["code"] == "internship"


def test_ready_and_held_open_exactly_the_jobs_their_numbers_count(client, service):
    """The idle runners' line says "2 internships": clicking it opens two rows; Ready opens the one that can start."""
    profile(service)
    start = ready(service, 1)
    held = [ready(service, n, title="Software Engineer Intern") for n in (2, 3)]
    old = ready(service, 4, listed=False)  # a job no board lists still counts, and still opens
    with service.db.exclusive() as s:
        s.get(Job, old).created_at = (datetime.now(UTC) - timedelta(days=90)).isoformat()
    said = client.get("/api/startable").json()
    assert said["startable"] == 1 and said["ready"] == 4
    base = "/api/job-board?include_sources=false&page=1&size=50"
    ready_page = client.get(base + "&fit=ready").json()
    assert [j["id"] for j in ready_page["jobs"]] == [start] == ready_page["selectable_ids"] and ready_page["total"] == 1
    held_page = client.get(base + "&fit=held").json()
    assert held_page["total"] == 3 and {j["id"] for j in held_page["jobs"]} == {*held, old}
    assert all(j["hold"]["reason"] for j in held_page["jobs"])
    internships = client.get(base + "&fit=held&hold=internship").json()
    assert {j["id"] for j in internships["jobs"]} == set(held) and internships["total"] == 2
    # The chips on the same answer carry the same numbers.
    assert held_page["fits"]["ready"] == 1 and held_page["fits"]["held"] == 3
    assert {h["code"]: h["count"] for h in held_page["holds"]} == {"internship": 2, "aged": 1}
