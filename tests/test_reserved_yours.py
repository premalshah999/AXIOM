"""A user reserves their own companies and job boards, the Job board's level filter, and one company not flooding the board (owner,
2026-11-01). Synthetic data only: every employer here is an example."""

import pytest

from jobpilot import autopilot, board_view, reserved
from jobpilot.admission import QueueBlocked
from jobpilot.db import Job, Setting
from jobpilot.features.discovery.roles import career_level, stated_years
from jobpilot.jobboard import BoardSettings
from jobpilot.schemas import Profile
from tests.test_board_view import listing, rows, setup


@pytest.fixture(autouse=True)
def fresh_rules():
    reserved._compiled.cache_clear()
    yield
    reserved._compiled.cache_clear()


def add_job(service, jid, company, url="https://jobs.example.test/1"):
    with service.db.exclusive() as s:
        s.add(Job(id=jid, identity=jid, url=url, company=company, title="Software Engineer", ats="greenhouse", status="ready", score=90,
                  location="Remote, United States"))
    return jid


def held(service):
    with service.db.session() as s:
        return set(reserved.held(s))


def screened(service, jid):
    return autopilot.screen_job(
        service.db, BoardSettings(auto_apply=True), Profile(target_roles=["Software Engineer"]), {"id": jid}, frozenset(),
        board_ids={jid}, integrated=True, use_master=True, data_dir=service.config.data_dir,
    )


def test_a_new_user_holds_nothing_until_they_reserve_something(client, service):
    add_job(service, "acme", "Acme Example")
    add_job(service, "amzn", "Amazon", "https://www.amazon.jobs/en/jobs/1")
    assert held(service) == set()  # the suggested FAANG list is a default they can switch on, not a hold they never chose
    out = client.post("/api/reserved/employers", json={"text": "Acme Example"})
    assert out.status_code == 200, out.text
    assert out.json()["reserved"] == "Acme Example" and [s["id"] for s in out.json()["sections"]] == ["mine"]
    assert held(service) == {"acme"}  # their own section is held without turning on the suggested ones


def test_a_reserved_job_never_passes_the_screen_or_admission_until_approved_and_returns_when_released(client, service):
    add_job(service, "acme", "Acme Example")
    client.post("/api/reserved/employers", json={"text": "Acme Example"})
    with pytest.raises(QueueBlocked) as blocked:
        screened(service, "acme")  # the dispatcher's own screen
    assert (blocked.value.code, blocked.value.outcome) == ("reserved_employer", "protected")
    with service.db.session() as s, pytest.raises(QueueBlocked):
        reserved.require_approval(s, s.get(Job, "acme"))  # queue admission
    assert reserved.held_ids(service.db) == {"acme"}
    assert client.post("/api/jobs/acme/approve-reserved", json={"apply": False}).status_code == 200
    assert screened(service, "acme")[0] == "ok"  # approved: the same screen lets it through
    assert held(service) == set()
    assert client.delete("/api/jobs/acme/approve-reserved").json()["removed"] is True
    assert held(service) == {"acme"}
    assert client.delete("/api/reserved/employers", params={"name": "acme example"}).status_code == 200  # any spelling of the name
    assert held(service) == set() and screened(service, "acme")[0] == "ok"
    assert client.delete("/api/reserved/employers", params={"name": "Acme Example"}).status_code == 404  # nothing left to remove


@pytest.mark.parametrize(
    "company, is_held",
    [
        ("Acme Example", True),
        ("acme example", True),
        ("ACME EXAMPLE, INC.", True),
        ("The Acme Example Company", True),
        ("Acme Example Careers", True),
        ("Acme Examples", False),
        ("Acme Exampleton", False),
        ("Bacme Example", False),
    ],
)
def test_name_variants_match_by_whole_words(client, service, company, is_held):
    add_job(service, "j", company)
    client.post("/api/reserved/employers", json={"text": "Acme Example"})
    assert (("j" in held(service))) is is_held


def test_a_job_board_link_reserves_that_board_and_a_shared_platform_alone_is_refused(client, service):
    add_job(service, "mine", "Whatever Name", "https://boards.example-ats.test/zeta-co/jobs/1")
    add_job(service, "other", "Whatever Name", "https://boards.example-ats.test/other-co/jobs/1")
    out = client.post("/api/reserved/employers", json={"text": "https://boards.example-ats.test/zeta-co/"})
    assert out.status_code == 200, out.text
    assert held(service) == {"mine"}
    refused = client.post("/api/reserved/employers", json={"text": "https://boards.greenhouse.io"})
    assert refused.status_code == 422 and "many employers" in refused.text
    assert reserved.employer_of("jobs.lever.co/zeta-co/") == ("Zeta Co", ["jobs.lever.co/zeta-co"])
    assert reserved.employer_of("https://www.example.test/careers/engineering?x=1") == ("Example", ["example.test"])  # an employer's own site


def test_a_default_can_be_removed_and_the_rest_stay(client, service):
    add_job(service, "amzn", "Amazon", "https://www.amazon.jobs/en/jobs/1")
    add_job(service, "goog", "Google", "https://careers.google.com/jobs/1")
    rules = client.get("/api/reserved").json()["rules"]
    client.put("/api/reserved", json={**rules, "enabled": True})
    assert held(service) == {"amzn", "goog"}
    out = client.delete("/api/reserved/employers", params={"name": "Amazon"})
    assert out.status_code == 200
    assert held(service) == {"goog"}
    names = {e["name"] for s in out.json()["rules"]["sections"] for e in s["employers"]}
    assert "Amazon" not in names and "Meta" in names


def test_reserving_a_company_already_in_a_default_list_adds_nothing_twice(client, service):
    client.post("/api/reserved/employers", json={"text": "Amazon"})
    sections = client.get("/api/reserved").json()["rules"]["sections"]
    assert [s["id"] for s in sections] == ["faang", "mango", "big_four"]  # it stays where it is; the switch decides


# ----- levels ---------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "title, text, stored, level",
    [
        ("Software Engineer Intern", "", "", "Intern"),
        ("Summer Internship, Data", "", "", "Intern"),
        ("Internal Tools Engineer", "", "", "Unknown"),
        ("New Grad 2027: Software Engineer", "", "", "New grad"),
        ("Algorithm Developer II - New College Grad", "", "", "New grad"),
        ("Graduate Software Engineer", "", "", "New grad"),
        ("Software Engineer I", "5+ years of experience", "", "Entry"),
        ("Junior Data Analyst", "", "", "Entry"),
        ("Associate Software Engineer", "", "", "Entry"),
        ("Software Engineer II", "", "", "Mid"),
        ("Machine Learning Engineer, Mid", "", "", "Mid"),
        ("Senior Software Engineer", "1 year of experience", "", "Senior"),
        ("Staff ML Engineer", "", "", "Senior"),
        ("SDE III", "", "", "Senior"),
        ("Associate Director, Data", "", "", "Senior"),
        ("Engineering Manager", "", "", "Senior"),
        ("Data Engineer", "Requires 7+ years of experience in data.", "", "Senior"),
        ("Data Engineer", "3+ years of experience with Python", "", "Mid"),
        ("Data Engineer", "0-1 years of experience", "", "Entry"),
        ("Data Engineer", "We are 60 years of experience across industries", "", "Unknown"),
        ("Data Engineer", "A Master's with 5 years of experience, or 3 years preferred", "", "Unknown"),
        ("Data Engineer", "This is an entry-level role.", "", "Entry"),
        ("Data Engineer", "", "New grad", "New grad"),
        ("Data Engineer", "", "Experience unspecified", "Unknown"),
        ("Data Engineer", "", "", "Unknown"),
    ],
)
def test_a_posting_level_is_read_from_its_title_then_its_text_and_unknown_is_said(title, text, stored, level):
    assert career_level(title, text, stored) == level


def test_the_largest_stated_requirement_outranks_a_skill_by_skill_one():
    assert stated_years("5+ years of experience in data\n3+ years of experience with Python\n") == 5
    assert stated_years("Bachelor's plus 3 years of experience\nA Master's with 1 year of experience") == 3


def unlabeled(service):
    """The test listings carry a feed's "New grad" label; these tests are about what the title and text say."""
    from sqlalchemy import update

    from jobpilot.db import JobListing

    with service.db.exclusive() as s:
        s.execute(update(JobListing).values(career_level=""))


def board(service, **filters):
    return rows(service, fit="all", **filters)


def test_every_level_filter_count_equals_its_rows(service):
    setup(service)
    names = [("a", "Software Engineer Intern"), ("b", "New Grad Software Engineer"), ("c", "Software Engineer I"), ("d", "Software Engineer II"),
             ("e", "Senior Software Engineer"), ("f", "Software Engineer"), ("g", "Data Engineer II")]
    for i, (jid, title) in enumerate(names):
        listing(service, jid, company=f"Company {jid}", title=title)
    unlabeled(service)
    facets = board(service)["facets"]["level"]
    assert [f["value"] for f in facets] == ["Intern", "New grad", "Entry", "Mid", "Senior", "Unknown"]
    assert {f["value"]: f["count"] for f in facets} == {"Intern": 1, "New grad": 1, "Entry": 1, "Mid": 2, "Senior": 1, "Unknown": 1}
    for option in facets:
        got = board(service, level=option["value"])
        assert got["total"] == option["count"] == len(got["rows"]), option
        assert {r["level"] for r in got["rows"]} <= {option["value"]}


# ----- one company does not flood the board -----------------------------------------------------------------------------


def test_a_company_with_many_rows_shows_its_best_few_and_says_how_many_more(service):
    setup(service)
    for i in range(7):
        listing(service, f"f{i}", company="Flood Example", title=f"Software Engineer {chr(65 + i)}", score=90 - i)
    listing(service, "o1", company="Other Example", title="Software Engineer", score=50)
    unlabeled(service)
    got = board(service)
    flood = [r for r in got["rows"] if r["company"] == "Flood Example"]
    assert [r["score"] for r in flood] == [90, 89, 88]  # its best three, in the order asked for
    assert [r["more"] for r in flood] == [0, 0, 4] and got["total"] == 4 and got["grouped"] is True
    # every other count is of the rows shown, and choosing the company shows them all
    assert {f["value"]: f["count"] for f in got["facets"]["company"]} == {"Flood Example": 7, "Other Example": 1}
    assert board(service, company="Flood Example")["total"] == 7 == len(board(service, company="Flood Example", size=100)["rows"])
    assert board(service, group=False)["total"] == 8
    assert {f["value"]: f["count"] for f in got["facets"]["level"]}["Unknown"] == 4
    assert board(service, level="Unknown")["total"] == 4
    assert {f["value"]: f["count"] for f in got["facets"]["fit"]}["all"] == got["total"]
