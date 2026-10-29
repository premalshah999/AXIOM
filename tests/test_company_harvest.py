"""Company lists become managed boards only when the board is the company (2026-10-29).

Slug probing finds boards that belong to somebody else ("Interco" -> `inter`, "Apex", "Mill", "Garage", "Sequoia"), so
a board is accepted on its own display name, and a short or prefix-only name must also show the company's domain."""

from datetime import UTC, datetime, timedelta

import httpx

from jobpilot.db import Source
from jobpilot.features.discovery import companies as co
from jobpilot.features.discovery import feeds
from jobpilot.jobboard import add_managed_sources, board_identity, managed_ids

JOBRIGHT = """
| Company | Job Title | Location | Work Model | Date Posted |
| ----- | --------- | --------- | ---- | ------- |
| **[Northwind Robotics](https://www.northwind.example)** | **[Software Engineer, New Grad](https://jobright.ai/jobs/info/1)** | Austin, TX | Hybrid | Oct 29 |
| ↳ | **[Data Engineer I](https://jobright.ai/jobs/info/2)** | Austin, TX | Hybrid | Oct 29 |
| **[Mill](https://mill.example)** | **[Software Engineer](https://jobright.ai/jobs/info/3)** | Remote | Remote | Oct 29 |
"""
SPEEDY = (
    '| <a href="https://www.contoso.example"><strong>Contoso Labs</strong></a> | Software Engineer I | NYC | $1/yr | '
    '<a href="https://x.example/1"><img/></a> | 1d |\n'
)
YC = (
    '[{"name": "Tailspin", "website": "https://tailspin.example", "isHiring": true, "status": "Active"},'
    '{"name": "Idle Co", "website": "https://idle.example", "isHiring": false, "status": "Active"},'
    '{"name": "Dead Co", "website": "https://dead.example", "isHiring": true, "status": "Inactive"}]'
)
LISTS = (
    ("jr", "jobright", "https://lists.example.test/jobright.md"),
    ("sa", "speedyapply", "https://lists.example.test/speedy.md"),
    ("yc", "yc", "https://lists.example.test/yc.json"),
)
BODY = {
    "https://lists.example.test/jobright.md": JOBRIGHT,
    "https://lists.example.test/speedy.md": SPEEDY,
    "https://lists.example.test/yc.json": YC,
}
NEW_GRAD_JOB = {"id": 1, "title": "Software Engineer, New Grad", "first_published": "2026-10-28T10:00:00Z",
                "absolute_url": "https://job-boards.greenhouse.io/northwindrobotics/jobs/1", "location": {"name": "Austin, TX"}}
SENIOR_JOB = {"id": 2, "title": "Staff Software Engineer", "first_published": "2026-10-28T10:00:00Z",
              "absolute_url": "https://job-boards.greenhouse.io/northwindrobotics/jobs/2", "location": {"name": "Austin, TX"}}
# slug -> (display name, postings text). `mill` is another employer's board and says nothing about mill.example;
# `tailspin` is a Lever board whose postings link to the company's domain.
GREENHOUSE = {
    "northwindrobotics": ("Northwind Robotics, Inc.", {"jobs": [NEW_GRAD_JOB, SENIOR_JOB]}),
    "mill": ("Mill", {"jobs": [{"id": 9, "title": "Chef", "content": "Join the Mill bakery at mill-bakery.example"}]}),
    "contosolabs": ("Contoso", {"jobs": [{"id": 3, "title": "Platform Engineer", "content": "see contoso.example/careers"}]}),
}


def web():
    def handler(request):
        url = str(request.url).split("?")[0]
        if url in BODY:
            return httpx.Response(200, text=BODY[url])
        if url.startswith("https://boards-api.greenhouse.io/v1/boards/"):
            rest = url.removeprefix("https://boards-api.greenhouse.io/v1/boards/")
            slug, _, tail = rest.partition("/")
            if slug in GREENHOUSE:
                name, jobs = GREENHOUSE[slug]
                return httpx.Response(200, json=jobs if tail == "jobs" else {"name": name, "content": ""})
        if url == "https://api.lever.co/v0/postings/tailspin":
            return httpx.Response(200, json=[{"text": "Backend Engineer", "hostedUrl": "https://jobs.lever.co/tailspin/1",
                                             "createdAt": 1793000000000, "categories": {"location": "Remote, US"},
                                             "descriptionPlain": "Tailspin builds things: tailspin.example"}])
        if url == "https://jobs.lever.co/tailspin":
            return httpx.Response(200, text="<html><title>Tailspin Jobs</title></html>")
        return httpx.Response(404)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_identity_ignores_legal_form_words_but_not_other_words():
    northwind = co.Company("Northwind Robotics")
    assert co.name_match("Northwind Robotics, Inc.", northwind) == "exact"
    assert co.name_match("Contoso", co.Company("Contoso Labs")) == "exact"
    assert co.name_match("OSI Batteries", co.Company("OSI Group")) == "prefix"  # ... which then needs osigroup.com
    assert co.name_match("Applied Intuition", co.Company("Applied Systems")) == "prefix"
    assert co.name_match("Interco Shipping", co.Company("Interco")) == "prefix"  # leading words alone prove nothing
    assert co.name_match("Interco", co.Company("Interco")) == "exact"
    assert co.name_match("inter", co.Company("Interco")) == ""
    assert co.name_match("FlexAI", co.Company("Flex")) == ""


def test_short_or_prefix_names_need_the_domain_in_the_boards_own_text():
    assert co.weak_name("Mill") and co.weak_name("Apex Inc") and not co.weak_name("Northwind Robotics")
    assert co.shows_domain("write to hr@mill.example now", "https://www.mill.example/about")
    assert co.shows_domain("see https://careers.mill.example/x", "https://mill.example")
    assert not co.shows_domain("see https://skmill.example/x and mill-bakery.example", "https://mill.example")
    assert not co.shows_domain("anything", "")
    assert co.domain_of("https://www.linkedin.com/company/mill") == ""  # Not the company's own site
    assert co.domain_of("https://shop.acme.co.uk/") == "acme.co.uk"


def test_slugs_come_from_the_name_and_the_domain_only():
    probes = co.slug_candidates("Northwind Robotics, Inc.", "https://www.northwind.example")
    slugs = {s for _, s in probes}
    assert {"northwindroboticsinc", "northwind-robotics-inc", "northwindrobotics", "northwind"} <= slugs
    assert "robotics" not in slugs  # A part of the name is another company's word
    assert {k for k, _ in probes} == set(co.KINDS)
    assert {k for k, _ in co.slug_candidates("Mill", "", kinds=("lever",))} == {"lever"}


def test_lists_parse_into_companies_once_with_a_website():
    assert [(c.name, c.website) for c in co.parse_jobright(JOBRIGHT)] == [
        ("Northwind Robotics", "https://www.northwind.example"), ("Mill", "https://mill.example")]
    assert [c.name for c in co.parse_speedyapply(SPEEDY)] == ["Contoso Labs"]
    assert [c.name for c in co.parse_yc(YC)] == ["Tailspin"]  # Not hiring and inactive companies are skipped


async def test_only_companies_that_own_the_board_are_found_and_counted():
    async with web() as http:
        companies = await co.fetch_companies(http, LISTS)
        assert [c.name for c in companies] == ["Northwind Robotics", "Mill", "Contoso Labs", "Tailspin"]
        boards, probed, probes = await co.find_boards(http, companies, now=datetime(2026, 10, 29, tzinfo=UTC))
    found = {(b.kind, b.ident): b for b in boards}
    # Mill's board shows no mill.example text; Contoso's board names "Contoso" and shows contoso.example.
    assert set(found) == {("greenhouse", "northwindrobotics"), ("greenhouse", "contosolabs"), ("lever", "tailspin")}
    assert found[("greenhouse", "northwindrobotics")].early == 1  # The staff role is not early-career
    assert found[("lever", "tailspin")].name == "Tailspin"
    assert len(probed) == 4 and probes > 4


async def test_the_probe_budget_stops_new_companies():
    async with web() as http:
        companies = await co.fetch_companies(http, LISTS)
        _, probed, probes = await co.find_boards(http, companies, max_probes=1, concurrency=1)
    assert len(probed) == 1 and probes <= 20  # The company that started is finished


def test_new_boards_exclude_stored_spellings_and_are_capped_best_first():
    boards = [
        co.Board("greenhouse", "alpha", "Alpha", "Alpha", early=1, early_target=0),
        co.Board("greenhouse", "Beta", "Beta", "Beta", early=5, early_target=5),
        co.Board("lever", "gamma", "Gamma", "Gamma", early=2, early_target=2),
        co.Board("lever", "stored", "Stored", "Stored", early=9, early_target=9),
    ]
    have = {feeds.board_key("lever", "stored")}
    assert [b.ident for b in co.chosen(boards, have, 2)] == ["Beta", "gamma"]
    assert [b.ident for b in co.chosen(boards, have, 10)] == ["Beta", "gamma", "alpha"]


def test_a_board_is_one_source_whatever_the_url_spelling():
    assert board_identity("https://boards.greenhouse.io/Acme/") == board_identity("https://job-boards.eu.greenhouse.io/acme")
    assert board_identity("https://jobs.lever.co/acme") != board_identity("https://jobs.ashbyhq.com/acme")
    assert board_identity("https://x.wd5.myworkdayjobs.com/Site") == "https://x.wd5.myworkdayjobs.com/site"


def stored(service, rows):
    db = service.db
    db.set_setting("job_board_settings", {"enabled": True, "interval_hours": 2})
    with db.exclusive() as s:
        for kind, name, url in rows:
            s.add(Source(name=name, kind=kind, url=url, cron="0 */2 * * *"))
    return db


def test_adding_skips_other_spellings_and_repeats_within_one_call(service):
    db = stored(service, [("greenhouse", "Acme", "https://boards.greenhouse.io/acme")])
    added = add_managed_sources(db, [
        ("greenhouse", "Acme", "https://job-boards.greenhouse.io/Acme"),
        ("lever", "Beta", "https://jobs.lever.co/beta"),
        ("lever", "Beta again", "https://jobs.lever.co/BETA/"),
    ])
    assert added == 1 and len(managed_ids(db)) == 1


async def test_harvest_adds_found_boards_once_and_remembers_empty_companies_for_two_weeks(service):
    db = stored(service, [("greenhouse", "Contoso", "https://job-boards.greenhouse.io/other")])  # Same name: skipped
    async with web() as http:
        report = await co.harvest_companies(db, client=http, lists=LISTS)
        assert report["state"] == "done" and report["added"] == 2  # Northwind (greenhouse) and Tailspin (lever)
        assert (await co.harvest_companies(db, client=http, lists=LISTS))["state"] == "skipped"
        later = datetime.now(UTC) + timedelta(days=2)
        again = await co.harvest_companies(db, client=http, lists=LISTS, at=later)
    assert again["added"] == 0 and again["probed"] == 0  # Everything was either stored or remembered
    with db.session() as s:
        urls = {r.url for r in s.query(Source).all()}
    assert {"https://job-boards.greenhouse.io/northwindrobotics", "https://jobs.lever.co/tailspin"} <= urls
    state = db.get_setting(co.HARVEST_KEY)
    assert "mill" in state["checked"]  # Mill's board belonged to somebody else; not asked again for 14 days


async def test_additions_are_capped_and_the_rest_wait_for_the_next_run(service):
    db = stored(service, [])
    async with web() as http:
        report = await co.harvest_companies(db, client=http, lists=LISTS, limit=1)
        assert report["added"] == 1 and report["boards"] == 3
        later = datetime.now(UTC) + timedelta(days=1)
        again = await co.harvest_companies(db, client=http, lists=LISTS, limit=5, at=later)
    assert again["added"] == 2  # The two boards that did not fit are found again


async def test_excluded_ats_are_not_probed_and_a_disabled_board_adds_nothing(service):
    db = stored(service, [])
    async with web() as http:
        db.set_setting("ats_focus", {"exclude": ["greenhouse"]})
        report = await co.harvest_companies(db, client=http, lists=LISTS)
        assert report["added"] == 1  # Only Tailspin on Lever
        db.set_setting("job_board_settings", {"enabled": False})
        assert (await co.harvest_companies(db, client=http, lists=LISTS, at=datetime.now(UTC) + timedelta(days=3)))["state"] == "disabled"
